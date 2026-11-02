"""Detect suspicious behaviour from the audit log (SEC-01).

Rules are evaluated on demand from the existing ``audit_logs`` columns, so no schema
change is required. Alerts never contain credentials, tokens or document content.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditLog, AuditResult

DOCUMENT_ACTIONS = tuple(
    action.value for action in AuditAction if action.value.startswith("DOCUMENT_")
)


@dataclass(frozen=True)
class DetectionThresholds:
    """Tunable limits for the detection rules."""

    window_minutes: int = 10
    failed_logins: int = 5
    denied_accesses: int = 5
    searches_per_minute: int = 30
    enumeration_run: int = 4


@dataclass(frozen=True)
class Alert:
    rule: str
    severity: str
    subject_type: str
    subject: str
    count: int
    window_minutes: int
    message: str


def longest_consecutive_run(values: Iterable[int]) -> int:
    """Return the length of the longest run of consecutive integers."""
    best = 0
    current = 0
    previous: int | None = None
    for value in sorted(set(values)):
        current = current + 1 if previous is not None and value == previous + 1 else 1
        best = max(best, current)
        previous = value
    return best


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _failed_logins(db: Session, since: datetime, limits: DetectionThresholds) -> list[Alert]:
    """R1: repeated failed logins from one IP address or against one account."""
    alerts: list[Alert] = []
    for column, subject_type in ((AuditLog.ip_address, "ip"), (AuditLog.user_id, "user")):
        rows = db.execute(
            select(column, func.count(AuditLog.id))
            .where(
                AuditLog.action == AuditAction.LOGIN.value,
                AuditLog.result == AuditResult.FAILURE.value,
                AuditLog.timestamp >= since,
                column.is_not(None),
            )
            .group_by(column)
            .having(func.count(AuditLog.id) >= limits.failed_logins)
        ).all()
        for subject, count in rows:
            alerts.append(
                Alert(
                    rule="R1",
                    severity="high",
                    subject_type=subject_type,
                    subject=str(subject),
                    count=count,
                    window_minutes=limits.window_minutes,
                    message=f"{count} failed logins in {limits.window_minutes} minutes",
                )
            )
    return alerts


def _denied_accesses(db: Session, since: datetime, limits: DetectionThresholds) -> list[Alert]:
    """R2: repeated refused document operations by one user."""
    rows = db.execute(
        select(AuditLog.user_id, func.count(AuditLog.id))
        .where(
            AuditLog.action.in_(DOCUMENT_ACTIONS),
            AuditLog.result == AuditResult.FAILURE.value,
            AuditLog.timestamp >= since,
            AuditLog.user_id.is_not(None),
        )
        .group_by(AuditLog.user_id)
        .having(func.count(AuditLog.id) >= limits.denied_accesses)
    ).all()
    return [
        Alert(
            rule="R2",
            severity="medium",
            subject_type="user",
            subject=str(user_id),
            count=count,
            window_minutes=limits.window_minutes,
            message=f"{count} refused document operations in {limits.window_minutes} minutes",
        )
        for user_id, count in rows
    ]


def _search_peaks(db: Session, now: datetime, limits: DetectionThresholds) -> list[Alert]:
    """R3: abnormal number of searches by one user during the last minute."""
    rows = db.execute(
        select(AuditLog.user_id, func.count(AuditLog.id))
        .where(
            AuditLog.action == AuditAction.DOCUMENT_SEARCH.value,
            AuditLog.timestamp >= now - timedelta(minutes=1),
            AuditLog.user_id.is_not(None),
        )
        .group_by(AuditLog.user_id)
        .having(func.count(AuditLog.id) >= limits.searches_per_minute)
    ).all()
    return [
        Alert(
            rule="R3",
            severity="medium",
            subject_type="user",
            subject=str(user_id),
            count=count,
            window_minutes=1,
            message=f"{count} searches in 1 minute",
        )
        for user_id, count in rows
    ]


def _enumeration(db: Session, since: datetime, limits: DetectionThresholds) -> list[Alert]:
    """R4: refused accesses on consecutive document ids (identifier guessing)."""
    rows = db.execute(
        select(AuditLog.user_id, AuditLog.resource_id).where(
            AuditLog.action.in_(DOCUMENT_ACTIONS),
            AuditLog.result == AuditResult.FAILURE.value,
            AuditLog.timestamp >= since,
            AuditLog.user_id.is_not(None),
            AuditLog.resource_id.is_not(None),
        )
    ).all()
    ids_by_user: dict[int, set[int]] = {}
    for user_id, resource_id in rows:
        ids_by_user.setdefault(user_id, set()).add(resource_id)

    alerts: list[Alert] = []
    for user_id, ids in ids_by_user.items():
        run = longest_consecutive_run(ids)
        if run >= limits.enumeration_run:
            alerts.append(
                Alert(
                    rule="R4",
                    severity="high",
                    subject_type="user",
                    subject=str(user_id),
                    count=run,
                    window_minutes=limits.window_minutes,
                    message=f"{run} consecutive document ids refused (possible enumeration)",
                )
            )
    return alerts


def detect_suspicious_activity(
    db: Session,
    *,
    now: datetime | None = None,
    thresholds: DetectionThresholds | None = None,
) -> list[Alert]:
    """Evaluate every rule and return the alerts, most severe first."""
    limits = thresholds or DetectionThresholds()
    current = now or _utc_now()
    since = current - timedelta(minutes=limits.window_minutes)
    alerts = [
        *_failed_logins(db, since, limits),
        *_denied_accesses(db, since, limits),
        *_search_peaks(db, current, limits),
        *_enumeration(db, since, limits),
    ]
    return sorted(alerts, key=lambda alert: (alert.severity != "high", alert.rule, alert.subject))
