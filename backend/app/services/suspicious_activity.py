"""On-demand detection of suspicious behaviour from audit_logs.

Nothing is persisted: alerts are recomputed on each call over a bounded
lookback period. Alerts are built from audit columns only, which never
contain tokens, passwords or secrets.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditLog, AuditReason, AuditResult
from app.models.department import Department
from app.models.user import User, UserRole
from app.schemas.security_alert import AlertRule, AlertSeverity, SecurityAlert
from app.services import document_service
from app.services.document_service import (
    find_by_id,
    load_index,
    parse_stored_name,
    sanitize_department_token,
)

DOCUMENT_ADMIN_ACTIONS = {
    AuditAction.DOCUMENT_CREATE.value,
    AuditAction.DOCUMENT_UPDATE.value,
    AuditAction.DOCUMENT_DELETE.value,
}
SEVERITY_ORDER = {AlertSeverity.HIGH: 0, AlertSeverity.MEDIUM: 1}


@dataclass(frozen=True)
class Peak:
    count: int
    events: list[AuditLog]


def find_peak(
    events: list[AuditLog],
    window: timedelta,
    key: Callable[[AuditLog], Hashable] | None = None,
) -> Peak:
    """Densest sliding window [t, t + window) over events sorted by timestamp.

    With ``key``, the count is the number of distinct keys inside the window.
    """
    best = Peak(0, [])
    seen: Counter = Counter()
    start = 0
    for end, event in enumerate(events):
        seen[key(event) if key else event.id] += 1
        while event.timestamp - events[start].timestamp >= window:
            dropped = key(events[start]) if key else events[start].id
            seen[dropped] -= 1
            if seen[dropped] == 0:
                del seen[dropped]
            start += 1
        if len(seen) > best.count:
            best = Peak(len(seen), events[start : end + 1])
    return best


def _severity(count: float, threshold: float) -> AlertSeverity:
    return AlertSeverity.HIGH if count >= 2 * threshold else AlertSeverity.MEDIUM


def _group(events: Iterable[AuditLog], subject: Callable[[AuditLog], Hashable | None]) -> dict:
    groups: dict[Hashable, list[AuditLog]] = defaultdict(list)
    for event in events:
        value = subject(event)
        if value is not None:
            groups[value].append(event)
    return groups


def _alert(
    rule: AlertRule,
    peak: Peak,
    threshold: float,
    window_minutes: int,
    *,
    users: dict[int, User],
    user_id: int | None = None,
    ip_address: str | None = None,
    severity: AlertSeverity | None = None,
    details: dict[str, object] | None = None,
) -> SecurityAlert:
    user = users.get(user_id) if user_id is not None else None
    return SecurityAlert(
        rule=rule,
        severity=severity or _severity(peak.count, threshold),
        user_id=user_id,
        user_email=user.email if user else None,
        ip_address=ip_address,
        event_count=peak.count,
        threshold=threshold,
        window_minutes=window_minutes,
        first_seen=peak.events[0].timestamp,
        last_seen=peak.events[-1].timestamp,
        details={
            "actions": sorted({event.action for event in peak.events}),
            **(details or {}),
        },
    )


def _threshold_alerts(
    rule: AlertRule,
    groups: dict,
    threshold: int,
    window_minutes: int,
    users: dict[int, User],
    by_ip: bool = False,
    token: str | None = None,
) -> list[SecurityAlert]:
    alerts = []
    for subject, events in groups.items():
        peak = find_peak(events, timedelta(minutes=window_minutes))
        if peak.count >= threshold:
            alerts.append(
                _alert(
                    rule,
                    peak,
                    threshold,
                    window_minutes,
                    users=users,
                    user_id=None if by_ip else subject,
                    ip_address=subject if by_ip else None,
                    details={
                        "subject": "ip" if by_ip else "user",
                        **({"token": token} if token else {}),
                    },
                )
            )
    return alerts


def _login_brute_force(events: list[AuditLog], users: dict[int, User]) -> list[SecurityAlert]:
    """R1a: failed logins per targeted account and per source IP."""
    failures = [
        e
        for e in events
        if e.action == AuditAction.LOGIN.value and e.result == AuditResult.FAILURE.value
    ]
    window = settings.alert_login_failures_window_minutes
    return _threshold_alerts(
        AlertRule.LOGIN_BRUTE_FORCE,
        _group(failures, lambda e: e.user_id),
        settings.alert_login_failures_user_threshold,
        window,
        users,
    ) + _threshold_alerts(
        AlertRule.LOGIN_BRUTE_FORCE,
        _group(failures, lambda e: e.ip_address),
        settings.alert_login_failures_ip_threshold,
        window,
        users,
        by_ip=True,
    )


def _token_abuse(events: list[AuditLog], users: dict[int, User]) -> list[SecurityAlert]:
    """R1b: rejected tokens per known user and per IP when anonymous; missing tokens per IP
    with their own higher threshold. SESSION_EXPIRED never counts.
    """
    failures = [e for e in events if e.action == AuditAction.AUTHENTICATION_FAILED.value]
    missing = [e for e in events if e.action == AuditAction.TOKEN_MISSING.value]
    window = settings.alert_token_failures_window_minutes
    return (
        _threshold_alerts(
            AlertRule.TOKEN_ABUSE,
            _group(failures, lambda e: e.user_id),
            settings.alert_token_failures_user_threshold,
            window,
            users,
        )
        + _threshold_alerts(
            AlertRule.TOKEN_ABUSE,
            _group(failures, lambda e: e.ip_address if e.user_id is None else None),
            settings.alert_token_failures_ip_threshold,
            window,
            users,
            by_ip=True,
        )
        + _threshold_alerts(
            AlertRule.TOKEN_ABUSE,
            _group(missing, lambda e: e.ip_address),
            settings.alert_missing_token_ip_threshold,
            window,
            users,
            by_ip=True,
            token="missing",
        )
    )


def _is_usable_index(index: object) -> bool:
    if not isinstance(index, dict) or not isinstance(index.get("documents"), list):
        return False
    return all(
        isinstance(entry, dict)
        and isinstance(entry.get("id"), int)
        and isinstance(entry.get("file_name"), str)
        for entry in index["documents"]
    )


@dataclass(frozen=True)
class DocumentResolution:
    cross_department: dict[int, str]  # event id -> foreign department name
    unresolved: dict[int, set[int]]  # user id -> unresolved document ids


def _resolve_document_failures(
    db: Session, events: list[AuditLog], users: dict[int, User]
) -> DocumentResolution:
    """Map employee DOCUMENT_VIEW failures to a foreign department using the current index.

    Read-only: load_index() never syncs nor writes. Unknown ids, deleted documents,
    malformed names or departments absent from the database are never counted.
    A valid JSON index with an unexpected structure is treated as unusable.
    """
    index = load_index()
    if not _is_usable_index(index):
        index = {"documents": []}
    departments = {
        sanitize_department_token(department.name): department.name
        for department in db.scalars(select(Department)).all()
    }
    cross_department: dict[int, str] = {}
    unresolved: dict[int, set[int]] = defaultdict(set)
    for event in events:
        user = users.get(event.user_id)
        if (
            event.action != AuditAction.DOCUMENT_VIEW.value
            or event.result != AuditResult.FAILURE.value
            or event.resource_id is None
            or user is None
            or user.role != UserRole.EMPLOYEE
        ):
            continue
        entry = find_by_id(index, event.resource_id)
        parsed = parse_stored_name(entry["file_name"]) if entry else None
        if parsed is None or parsed.department_token not in departments:
            unresolved[user.id].add(event.resource_id)
            continue
        own_token = sanitize_department_token(user.department.name) if user.department else None
        if parsed.department_token != own_token:
            cross_department[event.id] = departments[parsed.department_token]
    return DocumentResolution(cross_department, unresolved)


def _admin_probing_events(events: list[AuditLog], users: dict[int, User]) -> list[AuditLog]:
    """Operations forbidden to the caller's role.

    ACCESS_DENIED is only emitted by require_permission. Document CUD failures count
    for employees only: for an admin they are functional errors (404, 409, 400).
    """
    return [
        event
        for event in events
        if event.action == AuditAction.ACCESS_DENIED.value
        or (
            event.action in DOCUMENT_ADMIN_ACTIONS
            and event.result == AuditResult.FAILURE.value
            and event.user_id in users
            and users[event.user_id].role == UserRole.EMPLOYEE
        )
    ]


def _cross_department(
    events: list[AuditLog], resolution: DocumentResolution, users: dict[int, User]
) -> list[SecurityAlert]:
    """R3: distinct foreign-department documents denied to an employee."""
    threshold = settings.alert_cross_department_threshold
    window = settings.alert_cross_department_window_minutes
    attempts = [e for e in events if e.id in resolution.cross_department]
    alerts = []
    for user_id, user_events in _group(attempts, lambda e: e.user_id).items():
        peak = find_peak(user_events, timedelta(minutes=window), key=lambda e: e.resource_id)
        if peak.count < threshold:
            continue
        foreign = sorted({resolution.cross_department[e.id] for e in peak.events})
        severity = (
            AlertSeverity.HIGH
            if len(foreign) >= 2 or peak.count >= 2 * threshold
            else AlertSeverity.MEDIUM
        )
        alerts.append(
            _alert(
                AlertRule.CROSS_DEPARTMENT_PROBING,
                peak,
                threshold,
                window,
                users=users,
                user_id=user_id,
                severity=severity,
                details={
                    "document_ids": sorted({e.resource_id for e in peak.events}),
                    "departments": foreign,
                    "unresolved_document_ids": sorted(resolution.unresolved.get(user_id, ())),
                },
            )
        )
    return alerts


def _index_for_inference() -> dict | None:
    """Current index, only when it can prove that a document id does not exist.

    load_index() returns an empty index both for a missing and for an unreadable file, so
    the index file must exist and list at least one document. Read-only.
    """
    if not (document_service.DOCUMENTS_DIR / document_service.INDEX_NAME).is_file():
        return None
    index = load_index()
    if not _is_usable_index(index) or not index["documents"]:
        return None
    return index


def _nonexistent_document_attempts(
    events: list[AuditLog], users: dict[int, User]
) -> tuple[list[AuditLog], set[int]]:
    """Employee DOCUMENT_VIEW failures on document ids that do not exist.

    Events carrying a reason are trusted: only DOCUMENT_NOT_FOUND counts, never
    DEPARTMENT_FORBIDDEN or FILE_NOT_FOUND. Older events without reason count only when
    the current index proves the id is absent; an id still in the index (e.g. a document
    whose file is missing) never counts, and without a usable index they are ignored.
    Returns the attempts and the ids of the events that were inferred.
    """
    attempts: list[AuditLog] = []
    inferred: set[int] = set()
    index: dict | None = None
    index_loaded = False
    for event in events:
        user = users.get(event.user_id)
        if (
            event.action != AuditAction.DOCUMENT_VIEW.value
            or event.result != AuditResult.FAILURE.value
            or event.resource_id is None
            or user is None
            or user.role != UserRole.EMPLOYEE
        ):
            continue
        reason = (event.details or {}).get("reason")
        if reason is not None:
            if reason == AuditReason.DOCUMENT_NOT_FOUND.value:
                attempts.append(event)
            continue
        if not index_loaded:
            index = _index_for_inference()
            index_loaded = True
        if index is not None and find_by_id(index, event.resource_id) is None:
            attempts.append(event)
            inferred.add(event.id)
    return attempts, inferred


def _document_enumeration(events: list[AuditLog], users: dict[int, User]) -> list[SecurityAlert]:
    """Distinct non-existent document ids requested by one employee (id guessing)."""
    threshold = settings.alert_document_enumeration_threshold
    window = settings.alert_document_enumeration_window_minutes
    attempts, inferred = _nonexistent_document_attempts(events, users)
    alerts = []
    for user_id, user_events in _group(attempts, lambda e: e.user_id).items():
        peak = find_peak(user_events, timedelta(minutes=window), key=lambda e: e.resource_id)
        if peak.count < threshold:
            continue
        alerts.append(
            _alert(
                AlertRule.DOCUMENT_ENUMERATION,
                peak,
                threshold,
                window,
                users=users,
                user_id=user_id,
                details={
                    "document_ids": sorted({e.resource_id for e in peak.events}),
                    "inferred_document_ids": sorted(
                        {e.resource_id for e in peak.events if e.id in inferred}
                    ),
                },
            )
        )
    return alerts


def _search_burst(events: list[AuditLog], users: dict[int, User]) -> list[SecurityAlert]:
    """Many searches by one user in a short sliding window."""
    searches = [e for e in events if e.action == AuditAction.DOCUMENT_SEARCH.value]
    return _threshold_alerts(
        AlertRule.SEARCH_BURST,
        _group(searches, lambda e: e.user_id),
        settings.alert_search_burst_threshold,
        settings.alert_search_burst_window_minutes,
        users,
    )


def _activity_spike(
    db: Session,
    events: list[AuditLog],
    users: dict[int, User],
    lookback_start: datetime,
) -> list[SecurityAlert]:
    """R5: peak in the lookback period versus the mean of active windows over the
    baseline period B = [lookback_start - D, lookback_start).

    mu = sum(c_k) / |{k : c_k >= 1}| over aligned windows b_k of B, defined only when
    at least K_min windows are active. Threshold T = max(N_min, F * mu), or N_min
    without enough history. Alert when peak >= T, HIGH when peak >= 2 * T.
    """
    window = timedelta(minutes=settings.alert_spike_window_minutes)
    min_events = settings.alert_spike_min_events
    baseline_start = lookback_start - timedelta(days=settings.alert_spike_baseline_days)
    alerts = []
    for user_id, user_events in _group(events, lambda e: e.user_id).items():
        peak = find_peak(user_events, window)
        if peak.count < min_events:
            continue

        history = db.scalars(
            select(AuditLog.timestamp).where(
                AuditLog.user_id == user_id,
                AuditLog.timestamp >= baseline_start,
                AuditLog.timestamp < lookback_start,
            )
        ).all()
        active_windows = Counter((ts - baseline_start) // window for ts in history)
        if len(active_windows) >= settings.alert_spike_min_active_windows:
            mean = len(history) / len(active_windows)
            threshold = max(min_events, settings.alert_spike_factor * mean)
            baseline: dict[str, object] = {
                "baseline": "ok",
                "baseline_mean": round(mean, 2),
                "active_windows": len(active_windows),
            }
        else:
            threshold = min_events
            baseline = {"baseline": "insufficient", "active_windows": len(active_windows)}

        if peak.count >= threshold:
            alerts.append(
                _alert(
                    AlertRule.ACTIVITY_SPIKE,
                    peak,
                    threshold,
                    settings.alert_spike_window_minutes,
                    users=users,
                    user_id=user_id,
                    details=baseline,
                )
            )
    return alerts


def detect_alerts(
    db: Session,
    *,
    now: datetime | None = None,
    lookback_hours: int | None = None,
) -> list[SecurityAlert]:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    lookback_start = now - timedelta(hours=lookback_hours or settings.alert_lookback_hours)

    events = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.timestamp >= lookback_start, AuditLog.timestamp <= now)
            .order_by(AuditLog.timestamp, AuditLog.id)
        ).all()
    )
    user_ids = {e.user_id for e in events if e.user_id is not None}
    users = {
        user.id: user
        for user in db.scalars(
            select(User).options(selectinload(User.department)).where(User.id.in_(user_ids))
        ).all()
    }

    resolution = _resolve_document_failures(db, events, users)
    admin_probing = _admin_probing_events(events, users)
    admin_ids = {e.id for e in admin_probing}
    forbidden = [e for e in events if e.id in admin_ids or e.id in resolution.cross_department]

    alerts = [
        *_login_brute_force(events, users),
        *_token_abuse(events, users),
        *_threshold_alerts(
            AlertRule.REPEATED_FORBIDDEN,
            _group(forbidden, lambda e: e.user_id),
            settings.alert_forbidden_threshold,
            settings.alert_forbidden_window_minutes,
            users,
        ),
        *_cross_department(events, resolution, users),
        *_threshold_alerts(
            AlertRule.ADMIN_FEATURE_PROBING,
            _group(admin_probing, lambda e: e.user_id),
            settings.alert_admin_probing_threshold,
            settings.alert_admin_probing_window_minutes,
            users,
        ),
        *_activity_spike(db, events, users, lookback_start),
        *_document_enumeration(events, users),
        *_search_burst(events, users),
    ]
    alerts.sort(key=lambda alert: alert.last_seen, reverse=True)
    alerts.sort(key=lambda alert: SEVERITY_ORDER[alert.severity])
    return alerts
