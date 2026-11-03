# Tests de la détection des comportements suspects (SEC-01)

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.config import Settings, settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus
from app.schemas.security_alert import AlertRule, AlertSeverity
from app.services import document_service
from app.services.suspicious_activity import detect_alerts, find_peak

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)  # stored as naive UTC
IP = "203.0.113.7"

INDEXED_FILES = {
    1: "private__front_office__own_notes.pdf",
    2: "private__credit_risques__loan_book.pdf",
    3: "public__credit_risques__rates.pdf",
    4: "private__credit_risques__exposure.pdf",
    5: "private__direction_conformite__kyc_cases.pdf",
    6: "private__ghost_department__orphan.pdf",
    7: "not-a-stored-name.pdf",
    8: "public__direction_conformite__aml_policy.pdf",
}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    documents_dir = tmp_path / "documents"
    documents_dir.mkdir()
    monkeypatch.setattr(document_service, "DOCUMENTS_DIR", documents_dir)
    monkeypatch.setattr(document_service, "ARCHIVE_DIR", tmp_path / "archive")
    index = {
        "next_id": 9,
        "documents": [{"id": i, "file_name": name} for i, name in INDEXED_FILES.items()],
    }
    (documents_dir / document_service.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    for name in INDEXED_FILES.values():
        (documents_dir / name).write_bytes(b"%PDF-1.4 test")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        front = Department(name="Front Office")
        credit = Department(name="Credit Risques")
        compliance = Department(name="Direction Conformite")
        db.add_all([front, credit, compliance])
        db.flush()
        users = {
            key: User(
                email=f"{key}@example.com",
                password_hash=hash_password("Password123"),
                status=UserStatus.ACTIVE,
                role=role,
                department_id=front.id,
            )
            for key, role in [
                ("admin", UserRole.ADMIN),
                ("employee", UserRole.EMPLOYEE),
                ("other", UserRole.EMPLOYEE),
            ]
        }
        db.add_all(users.values())
        db.commit()
        ids = {key: user.id for key, user in users.items()}

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(user_key):
        role = "ADMIN" if user_key == "admin" else "EMPLOYEE"
        token = create_access_token(subject=str(ids[user_key]), role=role)
        return {"Authorization": f"Bearer {token}"}

    def add(
        action,
        count=1,
        *,
        user=None,
        start=NOW - timedelta(minutes=5),
        step=timedelta(seconds=10),
        result=AuditResult.FAILURE,
        resource_type=ResourceType.SESSION,
        resource_id=None,
        ip=IP,
        details=None,
    ):
        with TestingSessionLocal() as db:
            for i in range(count):
                db.add(
                    AuditLog(
                        user_id=ids[user] if user else None,
                        action=action.value,
                        resource_type=resource_type.value,
                        resource_id=resource_id(i) if callable(resource_id) else resource_id,
                        result=result.value,
                        timestamp=start + i * step,
                        ip_address=ip,
                        details=details,
                    )
                )
            db.commit()

    def detect(**kwargs):
        with TestingSessionLocal() as db:
            return detect_alerts(db, now=kwargs.pop("now", NOW), **kwargs)

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "headers": headers,
                "ids": ids,
                "add": add,
                "detect": detect,
                "documents_dir": documents_dir,
                "session_factory": TestingSessionLocal,
            }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _rules(alerts):
    return [alert.rule for alert in alerts]


def _view_failures(env, document_ids, user="employee", start=NOW - timedelta(minutes=10)):
    for offset, document_id in enumerate(document_ids):
        env["add"](
            AuditAction.DOCUMENT_VIEW,
            user=user,
            start=start + timedelta(seconds=30 * offset),
            resource_type=ResourceType.DOCUMENT,
            resource_id=document_id,
        )


# --- Sliding window --------------------------------------------------------------


def test_find_peak_window_is_half_open():
    events = [
        AuditLog(id=i, timestamp=NOW + timedelta(minutes=minute))
        for i, minute in enumerate([0, 5, 10, 10, 30])
    ]
    peak = find_peak(events, timedelta(minutes=10))
    assert peak.count == 3  # [5, 15) holds 5, 10, 10; 0 and 10 are exactly W apart
    assert [e.timestamp.minute for e in peak.events] == [5, 10, 10]


def test_find_peak_counts_distinct_keys():
    events = [
        AuditLog(id=i, resource_id=rid, timestamp=NOW + timedelta(seconds=i))
        for i, rid in enumerate([1, 1, 1, 2])
    ]
    assert find_peak(events, timedelta(minutes=1), key=lambda e: e.resource_id).count == 2


# --- Endpoint access ---------------------------------------------------------------


def test_alerts_require_admin(ctx):
    client = ctx["client"]
    assert client.get("/audit/alerts").status_code == 401
    assert client.get("/audit/alerts", headers=ctx["headers"]("employee")).status_code == 403
    with ctx["session_factory"]() as db:
        denied = db.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.ACCESS_DENIED.value)
        ).all()
    assert len(denied) == 1
    assert denied[0].resource_type == ResourceType.AUDIT_LOG.value

    response = client.get("/audit/alerts", headers=ctx["headers"]("admin"))
    assert response.status_code == 200
    assert response.json() == []


def test_no_events_no_alerts(ctx):
    assert ctx["detect"]() == []


@pytest.mark.parametrize("lookback", [0, 169])
def test_lookback_is_bounded(ctx, lookback):
    response = ctx["client"].get(
        f"/audit/alerts?lookback_hours={lookback}", headers=ctx["headers"]("admin")
    )
    assert response.status_code == 422


# --- R1a LOGIN_BRUTE_FORCE ---------------------------------------------------------


def test_r1a_login_failures_from_one_ip(ctx):
    ctx["add"](AuditAction.LOGIN, 10, resource_type=ResourceType.USER)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.LOGIN_BRUTE_FORCE]
    assert alerts[0].ip_address == IP
    assert alerts[0].user_id is None
    assert alerts[0].event_count == 10
    assert alerts[0].details["subject"] == "ip"


def test_r1a_below_ip_threshold(ctx):
    ctx["add"](AuditAction.LOGIN, 9, resource_type=ResourceType.USER)
    assert ctx["detect"]() == []


def test_r1a_failures_spread_over_time_do_not_alert(ctx):
    ctx["add"](
        AuditAction.LOGIN,
        10,
        resource_type=ResourceType.USER,
        start=NOW - timedelta(hours=3),
        step=timedelta(minutes=16),
    )
    assert ctx["detect"]() == []


def test_r1a_login_failures_on_one_account(ctx):
    ctx["add"](AuditAction.LOGIN, 5, user="employee", resource_type=ResourceType.USER)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.LOGIN_BRUTE_FORCE]
    assert alerts[0].user_id == ctx["ids"]["employee"]
    assert alerts[0].user_email == "employee@example.com"


def test_r1a_successful_logins_are_ignored(ctx):
    ctx["add"](
        AuditAction.LOGIN,
        20,
        user="employee",
        result=AuditResult.SUCCESS,
        resource_type=ResourceType.USER,
    )
    assert AlertRule.LOGIN_BRUTE_FORCE not in _rules(ctx["detect"]())


# --- R1b TOKEN_ABUSE ---------------------------------------------------------------


def test_r1b_anonymous_token_failures_from_one_ip(ctx):
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 10)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.TOKEN_ABUSE]
    assert alerts[0].ip_address == IP


def test_r1b_session_expiries_never_alert(ctx):
    ctx["add"](AuditAction.SESSION_EXPIRED, 50)
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 9)
    assert ctx["detect"]() == []


def test_r1b_revoked_token_reuse_by_known_user(ctx):
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 5, user="employee")
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.TOKEN_ABUSE]
    assert alerts[0].user_id == ctx["ids"]["employee"]


def test_r1b_known_user_events_do_not_feed_the_ip_rule(ctx):
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 4, user="employee")
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 9)
    assert ctx["detect"]() == []


def test_r1b_missing_tokens_use_their_own_ip_threshold(ctx):
    ctx["add"](AuditAction.TOKEN_MISSING, 29)
    assert ctx["detect"]() == []
    ctx["add"](AuditAction.TOKEN_MISSING, 1)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.TOKEN_ABUSE]
    assert alerts[0].ip_address == IP
    assert alerts[0].threshold == 30
    assert alerts[0].details["token"] == "missing"


def test_r1b_missing_and_rejected_tokens_are_not_summed(ctx):
    ctx["add"](AuditAction.TOKEN_MISSING, 29)
    ctx["add"](AuditAction.AUTHENTICATION_FAILED, 9)
    assert ctx["detect"]() == []


def test_token_missing_feeds_no_other_rule(ctx):
    ctx["add"](AuditAction.TOKEN_MISSING, 120, step=timedelta(seconds=1))
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.TOKEN_ABUSE]
    assert alerts[0].details["token"] == "missing"
    assert alerts[0].severity == AlertSeverity.HIGH


# --- R2 REPEATED_FORBIDDEN / R4 ADMIN_FEATURE_PROBING --------------------------------


def test_r2_counts_each_event_once_across_r3_and_r4(ctx):
    ctx["add"](AuditAction.ACCESS_DENIED, 5, user="employee", resource_type=ResourceType.USER)
    _view_failures(ctx, [2, 3, 4, 5, 8])
    alerts = ctx["detect"]()
    r2 = [a for a in alerts if a.rule == AlertRule.REPEATED_FORBIDDEN]
    assert len(r2) == 1
    assert r2[0].event_count == 10
    assert r2[0].details["actions"] == ["ACCESS_DENIED", "DOCUMENT_VIEW"]
    assert sorted(_rules(alerts)) == sorted(
        [
            AlertRule.REPEATED_FORBIDDEN,
            AlertRule.CROSS_DEPARTMENT_PROBING,
            AlertRule.ADMIN_FEATURE_PROBING,
        ]
    )


def test_r2_and_r4_on_repeated_admin_route_denials(ctx):
    ctx["add"](AuditAction.ACCESS_DENIED, 10, user="employee", resource_type=ResourceType.USER)
    alerts = ctx["detect"]()
    assert sorted(_rules(alerts)) == sorted(
        [AlertRule.REPEATED_FORBIDDEN, AlertRule.ADMIN_FEATURE_PROBING]
    )
    r4 = next(a for a in alerts if a.rule == AlertRule.ADMIN_FEATURE_PROBING)
    assert r4.severity == AlertSeverity.HIGH  # 10 >= 2 * 3


def test_r4_threshold(ctx):
    ctx["add"](AuditAction.ACCESS_DENIED, 2, user="employee", resource_type=ResourceType.AUDIT_LOG)
    assert ctx["detect"]() == []
    ctx["add"](AuditAction.ACCESS_DENIED, 1, user="employee", resource_type=ResourceType.AUDIT_LOG)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.ADMIN_FEATURE_PROBING]
    assert alerts[0].severity == AlertSeverity.MEDIUM


def test_r4_employee_document_mutations(ctx):
    for action in (
        AuditAction.DOCUMENT_CREATE,
        AuditAction.DOCUMENT_UPDATE,
        AuditAction.DOCUMENT_DELETE,
    ):
        ctx["add"](action, user="employee", resource_type=ResourceType.DOCUMENT)
    assert _rules(ctx["detect"]()) == [AlertRule.ADMIN_FEATURE_PROBING]


def test_admin_functional_failures_never_alert(ctx):
    # 35 events: enough for R2 and R4 if admins were counted, below the R5 minimum.
    for action in (
        AuditAction.DOCUMENT_CREATE,
        AuditAction.DOCUMENT_UPDATE,
        AuditAction.DOCUMENT_DELETE,
        AuditAction.USER_UPDATE,
        AuditAction.USER_STATUS_UPDATE,
        AuditAction.USER_CREATE,
    ):
        ctx["add"](action, 5, user="admin", resource_type=ResourceType.DOCUMENT, resource_id=99)
    ctx["add"](
        AuditAction.DOCUMENT_VIEW,
        5,
        user="admin",
        resource_type=ResourceType.DOCUMENT,
        resource_id=lambda i: 2 + i % 3,
    )
    assert ctx["detect"]() == []


# --- R3 CROSS_DEPARTMENT_PROBING ---------------------------------------------------


def test_r3_three_documents_from_one_foreign_department(ctx):
    _view_failures(ctx, [2, 3, 4])
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.CROSS_DEPARTMENT_PROBING]
    alert = alerts[0]
    assert alert.severity == AlertSeverity.MEDIUM
    assert alert.event_count == 3
    assert alert.details["document_ids"] == [2, 3, 4]
    assert alert.details["departments"] == ["Credit Risques"]


def test_r3_two_foreign_departments_is_high(ctx):
    _view_failures(ctx, [2, 3, 5])
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.CROSS_DEPARTMENT_PROBING]
    assert alerts[0].severity == AlertSeverity.HIGH
    assert alerts[0].details["departments"] == ["Credit Risques", "Direction Conformite"]


def test_r3_same_document_repeated_does_not_alert(ctx):
    _view_failures(ctx, [2] * 5)
    assert ctx["detect"]() == []


def test_r3_unresolved_and_own_department_failures_are_not_counted(ctx):
    # 404 unknown ids, orphan department, malformed name, own department (missing file)
    _view_failures(ctx, [404, 405, 406, 6, 7, 1, 1])
    assert ctx["detect"]() == []


def test_r3_reports_unresolved_ids_without_counting_them(ctx):
    _view_failures(ctx, [404, 6, 2, 3, 4])
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.CROSS_DEPARTMENT_PROBING]
    assert alerts[0].event_count == 3
    assert alerts[0].details["unresolved_document_ids"] == [6, 404]


def test_r3_admin_cross_department_failures_are_ignored(ctx):
    _view_failures(ctx, [2, 3, 4, 5], user="admin")
    assert ctx["detect"]() == []


def test_r3_reads_index_without_writing_it(ctx):
    index_file = ctx["documents_dir"] / document_service.INDEX_NAME
    (ctx["documents_dir"] / "private__credit_risques__unindexed.pdf").write_bytes(b"%PDF-1.4")
    before = index_file.read_bytes()
    _view_failures(ctx, [2, 3, 4])
    ctx["detect"]()
    assert index_file.read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"next_id": 3}',
        '{"documents": {"2": "private__credit_risques__loan_book.pdf"}}',
        '{"documents": [{"id": "2", "file_name": "private__credit_risques__loan_book.pdf"}]}',
        '{"documents": [{"id": 2}]}',
        '{"documents": [2, 3, 4]}',
    ],
)
def test_r3_malformed_index_is_unusable_without_server_error(ctx, content):
    index_file = ctx["documents_dir"] / document_service.INDEX_NAME
    index_file.write_text(content, encoding="utf-8")
    _view_failures(ctx, [2, 3, 4, 5])
    assert ctx["detect"]() == []

    response = ctx["client"].get("/audit/alerts", headers=ctx["headers"]("admin"))
    assert response.status_code == 200
    assert response.json() == []
    assert index_file.read_text(encoding="utf-8") == content


def test_r3_missing_index_resolves_nothing(ctx):
    (ctx["documents_dir"] / document_service.INDEX_NAME).unlink()
    _view_failures(ctx, [2, 3, 4, 5])
    assert ctx["detect"]() == []
    assert not (ctx["documents_dir"] / document_service.INDEX_NAME).exists()


def test_r2_counts_resolved_cross_department_denials(ctx):
    _view_failures(ctx, [2, 3, 4, 5] * 3)
    assert sorted(_rules(ctx["detect"]())) == sorted(
        [AlertRule.REPEATED_FORBIDDEN, AlertRule.CROSS_DEPARTMENT_PROBING]
    )


# --- R5 ACTIVITY_SPIKE -------------------------------------------------------------


def _spike(env, count, user="employee"):
    # Document views, not searches: a dense run of searches is also a SEARCH_BURST.
    env["add"](
        AuditAction.DOCUMENT_VIEW,
        count,
        user=user,
        start=NOW - timedelta(minutes=9),
        step=timedelta(seconds=1),
        result=AuditResult.SUCCESS,
        resource_type=ResourceType.DOCUMENT,
    )


def _history(env, windows, per_window, user="employee", anchor=NOW - timedelta(hours=24)):
    # One burst per active 10-minute window, inside B = [anchor - 7 days, anchor)
    for k in range(windows):
        env["add"](
            AuditAction.DOCUMENT_VIEW,
            per_window,
            user=user,
            start=anchor - timedelta(hours=2 * (k + 1)),
            step=timedelta(seconds=1),
            result=AuditResult.SUCCESS,
            resource_type=ResourceType.DOCUMENT,
            resource_id=1,
        )


def test_r5_spike_without_history(ctx):
    _spike(ctx, 60)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.ACTIVITY_SPIKE]
    assert alerts[0].severity == AlertSeverity.MEDIUM
    assert alerts[0].threshold == 60
    assert alerts[0].details["baseline"] == "insufficient"


def test_r5_below_minimum(ctx):
    _spike(ctx, 59)
    assert ctx["detect"]() == []


def test_r5_high_severity(ctx):
    _spike(ctx, 120)
    assert ctx["detect"]()[0].severity == AlertSeverity.HIGH


def test_r5_busy_user_baseline_raises_threshold(ctx):
    _history(ctx, windows=6, per_window=20)  # mu = 20 -> T = max(60, 5 * 20) = 100
    _spike(ctx, 60)
    assert ctx["detect"]() == []
    _spike(ctx, 40)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.ACTIVITY_SPIKE]
    assert alerts[0].threshold == 100
    assert alerts[0].details == {
        "actions": ["DOCUMENT_VIEW"],
        "baseline": "ok",
        "baseline_mean": 20.0,
        "active_windows": 6,
    }


def test_r5_empty_windows_are_excluded_from_the_mean(ctx):
    _history(ctx, windows=6, per_window=2)  # 12 events over 6 of 1008 windows -> mu = 2
    _spike(ctx, 60)
    alerts = ctx["detect"]()
    assert alerts[0].details["baseline_mean"] == 2.0
    assert alerts[0].threshold == 60


def test_r5_lookback_period_is_excluded_from_the_baseline(ctx):
    # Same bursts, but inside the analysis period: they must not count as history.
    _history(ctx, windows=6, per_window=20, anchor=NOW - timedelta(hours=1))
    _spike(ctx, 60)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.ACTIVITY_SPIKE]
    assert alerts[0].details["baseline"] == "insufficient"


def test_r5_history_older_than_baseline_is_ignored(ctx):
    _history(ctx, windows=6, per_window=20, anchor=NOW - timedelta(days=8, hours=1))
    _spike(ctx, 60)
    assert ctx["detect"]()[0].details["baseline"] == "insufficient"


def test_r5_too_few_active_windows_is_insufficient(ctx):
    _history(ctx, windows=5, per_window=50)
    _spike(ctx, 60)
    alerts = ctx["detect"]()
    assert alerts[0].details == {
        "actions": ["DOCUMENT_VIEW"],
        "baseline": "insufficient",
        "active_windows": 5,
    }


# --- General behaviour -------------------------------------------------------------


def test_events_outside_lookback_are_ignored(ctx):
    ctx["add"](
        AuditAction.LOGIN, 10, resource_type=ResourceType.USER, start=NOW - timedelta(hours=2)
    )
    assert ctx["detect"](lookback_hours=1) == []
    assert _rules(ctx["detect"](lookback_hours=3)) == [AlertRule.LOGIN_BRUTE_FORCE]


def test_thresholds_are_configurable(ctx, monkeypatch):
    ctx["add"](AuditAction.LOGIN, 3, resource_type=ResourceType.USER)
    assert ctx["detect"]() == []
    monkeypatch.setattr(settings, "alert_login_failures_ip_threshold", 3)
    assert _rules(ctx["detect"]()) == [AlertRule.LOGIN_BRUTE_FORCE]


def test_one_alert_per_rule_and_subject(ctx):
    ctx["add"](
        AuditAction.LOGIN,
        40,
        resource_type=ResourceType.USER,
        start=NOW - timedelta(hours=1),
        step=timedelta(seconds=90),
    )
    alerts = ctx["detect"]()
    assert len(alerts) == 1
    assert alerts[0].event_count == 10  # densest 15-minute window
    assert alerts[0].severity == AlertSeverity.MEDIUM


def test_api_filters_and_sorting(ctx):
    now = datetime.now(UTC).replace(tzinfo=None)
    ctx["add"](
        AuditAction.LOGIN, 10, resource_type=ResourceType.USER, start=now - timedelta(minutes=5)
    )
    ctx["add"](
        AuditAction.ACCESS_DENIED,
        6,
        user="other",
        resource_type=ResourceType.USER,
        start=now - timedelta(minutes=10),
    )
    admin = ctx["headers"]("admin")

    alerts = ctx["client"].get("/audit/alerts", headers=admin).json()
    assert [a["rule"] for a in alerts] == ["ADMIN_FEATURE_PROBING", "LOGIN_BRUTE_FORCE"]
    assert [a["severity"] for a in alerts] == ["HIGH", "MEDIUM"]

    by_rule = ctx["client"].get("/audit/alerts?rule=LOGIN_BRUTE_FORCE", headers=admin).json()
    assert [a["rule"] for a in by_rule] == ["LOGIN_BRUTE_FORCE"]
    high = ctx["client"].get("/audit/alerts?severity=HIGH", headers=admin).json()
    assert [a["rule"] for a in high] == ["ADMIN_FEATURE_PROBING"]
    assert ctx["client"].get("/audit/alerts?rule=NOPE", headers=admin).status_code == 422


def test_end_to_end_real_denials_raise_cross_department_alert(ctx):
    employee = ctx["headers"]("employee")
    for document_id in (2, 3, 4):
        response = ctx["client"].get(f"/documents/{document_id}", headers=employee)
        assert response.status_code == 403

    alerts = ctx["client"].get("/audit/alerts", headers=ctx["headers"]("admin")).json()
    assert [a["rule"] for a in alerts] == ["CROSS_DEPARTMENT_PROBING"]
    assert alerts[0]["user_email"] == "employee@example.com"
    assert alerts[0]["details"]["document_ids"] == [2, 3, 4]


def test_alerts_never_expose_tokens_or_passwords(ctx):
    employee_token = ctx["headers"]("employee")["Authorization"].split(" ", 1)[1]
    for _ in range(10):
        ctx["client"].post(
            "/auth/login", json={"email": "employee@example.com", "password": "Sup3r-Secret!"}
        )
        ctx["client"].get("/audit", headers={"Authorization": "Bearer forged.token.value"})

    body = ctx["client"].get("/audit/alerts", headers=ctx["headers"]("admin")).text
    assert "LOGIN_BRUTE_FORCE" in body
    assert "TOKEN_ABUSE" in body
    for secret in (
        "Sup3r-Secret!",
        "forged.token.value",
        employee_token,
        "Bearer",
        settings.jwt_secret_key,
    ):
        assert secret not in body


# --- DOCUMENT_ENUMERATION (SEC-01 T5) -----------------------------------------------

MISSING_IDS = [100, 101, 102, 103]  # absent from the fixture index (ids 1..8)


def _view_attempts(env, document_ids, *, reason=None, user="employee", step_seconds=30):
    for offset, document_id in enumerate(document_ids):
        env["add"](
            AuditAction.DOCUMENT_VIEW,
            user=user,
            start=NOW - timedelta(minutes=20) + timedelta(seconds=step_seconds * offset),
            resource_type=ResourceType.DOCUMENT,
            resource_id=document_id,
            details={"reason": reason} if reason else None,
        )


def _enumeration_alerts(env):
    return [a for a in env["detect"]() if a.rule == AlertRule.DOCUMENT_ENUMERATION]


def test_enumeration_from_explicit_not_found_reasons(ctx):
    _view_attempts(ctx, MISSING_IDS, reason="DOCUMENT_NOT_FOUND")
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.DOCUMENT_ENUMERATION]
    [alert] = alerts
    assert alert.user_id == ctx["ids"]["employee"]
    assert alert.event_count == 4
    assert alert.threshold == 4
    assert alert.window_minutes == 30
    assert alert.severity == AlertSeverity.MEDIUM
    assert alert.details["document_ids"] == MISSING_IDS
    assert alert.details["inferred_document_ids"] == []


def test_enumeration_below_threshold(ctx):
    _view_attempts(ctx, MISSING_IDS[:3], reason="DOCUMENT_NOT_FOUND")
    assert ctx["detect"]() == []


def test_enumeration_counts_distinct_ids_only(ctx):
    _view_attempts(ctx, [100] * 8, reason="DOCUMENT_NOT_FOUND")
    assert ctx["detect"]() == []


def test_enumeration_uses_a_sliding_30_minute_window(ctx):
    # 4 distinct ids, 11 minutes apart: never 4 inside the same 30-minute window.
    _view_attempts(ctx, MISSING_IDS, reason="DOCUMENT_NOT_FOUND", step_seconds=660)
    assert _enumeration_alerts(ctx) == []


@pytest.mark.parametrize("reason", ["DEPARTMENT_FORBIDDEN", "FILE_NOT_FOUND", "ROLE_FORBIDDEN"])
def test_enumeration_ignores_other_reasons(ctx, reason):
    _view_attempts(ctx, MISSING_IDS, reason=reason)
    assert _enumeration_alerts(ctx) == []


def test_enumeration_infers_older_events_absent_from_the_index(ctx):
    _view_attempts(ctx, MISSING_IDS)  # no reason: written before the reasons existed
    [alert] = _enumeration_alerts(ctx)
    assert alert.details["document_ids"] == MISSING_IDS
    assert alert.details["inferred_document_ids"] == MISSING_IDS


def test_enumeration_mixes_explicit_and_inferred_events(ctx):
    _view_attempts(ctx, MISSING_IDS[:2], reason="DOCUMENT_NOT_FOUND")
    _view_attempts(ctx, MISSING_IDS[2:])
    [alert] = _enumeration_alerts(ctx)
    assert alert.details["document_ids"] == MISSING_IDS
    assert alert.details["inferred_document_ids"] == MISSING_IDS[2:]


def test_older_events_on_ids_still_in_the_index_are_never_counted(ctx):
    # Ids 2..5 exist; older failures on them may be refusals or missing files.
    _view_attempts(ctx, [2, 3, 4, 5, 6, 7])
    assert _enumeration_alerts(ctx) == []


def test_existing_document_with_missing_file_is_never_counted(ctx):
    (ctx["documents_dir"] / "private__credit_risques__loan_book.pdf").unlink()  # id 2
    _view_attempts(ctx, [2], reason="FILE_NOT_FOUND")
    _view_attempts(ctx, [2])  # older event on the same id, without reason
    _view_attempts(ctx, MISSING_IDS[:3])
    assert _enumeration_alerts(ctx) == []

    _view_attempts(ctx, MISSING_IDS[3:])
    [alert] = _enumeration_alerts(ctx)
    assert 2 not in alert.details["document_ids"]
    assert alert.details["document_ids"] == MISSING_IDS


@pytest.mark.parametrize(
    "index_content",
    [
        None,  # index file missing
        "{not json",  # unreadable
        '{"next_id": 1, "documents": []}',  # empty: proves nothing
        '{"documents": {"100": "x.pdf"}}',  # unexpected structure
    ],
)
def test_ambiguous_older_events_are_ignored_without_a_usable_index(ctx, index_content):
    index_file = ctx["documents_dir"] / document_service.INDEX_NAME
    if index_content is None:
        index_file.unlink()
    else:
        index_file.write_text(index_content, encoding="utf-8")
    _view_attempts(ctx, MISSING_IDS)
    assert _enumeration_alerts(ctx) == []

    # Explicit reasons do not depend on the index.
    _view_attempts(ctx, [200, 201, 202, 203], reason="DOCUMENT_NOT_FOUND")
    [alert] = _enumeration_alerts(ctx)
    assert alert.details["document_ids"] == [200, 201, 202, 203]


def test_enumeration_ignores_administrators(ctx):
    _view_attempts(ctx, MISSING_IDS, reason="DOCUMENT_NOT_FOUND", user="admin")
    _view_attempts(ctx, [104, 105, 106, 107], user="admin")
    assert ctx["detect"]() == []


def test_enumeration_reads_the_index_without_writing_it(ctx):
    index_file = ctx["documents_dir"] / document_service.INDEX_NAME
    (ctx["documents_dir"] / "private__credit_risques__unindexed.pdf").write_bytes(b"%PDF-1.4")
    before = index_file.read_bytes()
    _view_attempts(ctx, MISSING_IDS)
    ctx["detect"]()
    assert index_file.read_bytes() == before


def test_end_to_end_real_not_found_requests_raise_enumeration(ctx):
    employee = ctx["headers"]("employee")
    for document_id in MISSING_IDS:
        assert ctx["client"].get(f"/documents/{document_id}", headers=employee).status_code == 404

    with ctx["session_factory"]() as db:
        reasons = db.scalars(
            select(AuditLog.details).where(AuditLog.action == AuditAction.DOCUMENT_VIEW.value)
        ).all()
    assert reasons == [{"reason": "DOCUMENT_NOT_FOUND"}] * 4

    alerts = (
        ctx["client"]
        .get("/audit/alerts?rule=DOCUMENT_ENUMERATION", headers=ctx["headers"]("admin"))
        .json()
    )
    assert [a["rule"] for a in alerts] == ["DOCUMENT_ENUMERATION"]
    assert alerts[0]["details"]["document_ids"] == MISSING_IDS
    assert alerts[0]["details"]["inferred_document_ids"] == []


# --- SEARCH_BURST (SEC-01 T5) -------------------------------------------------------


def _searches(env, count, *, user="employee", step=timedelta(seconds=1)):
    env["add"](
        AuditAction.DOCUMENT_SEARCH,
        count,
        user=user,
        start=NOW - timedelta(minutes=5),
        step=step,
        result=AuditResult.SUCCESS,
        resource_type=ResourceType.DOCUMENT,
    )


def test_search_burst_within_one_minute(ctx):
    _searches(ctx, 30)
    alerts = ctx["detect"]()
    assert _rules(alerts) == [AlertRule.SEARCH_BURST]
    assert alerts[0].event_count == 30
    assert alerts[0].threshold == 30
    assert alerts[0].window_minutes == 1
    assert alerts[0].user_id == ctx["ids"]["employee"]


def test_search_burst_below_threshold(ctx):
    _searches(ctx, 29)
    assert ctx["detect"]() == []


def test_searches_spread_over_several_minutes_do_not_alert(ctx):
    _searches(ctx, 30, step=timedelta(seconds=6))  # 30 searches over 3 minutes
    assert ctx["detect"]() == []


def test_search_burst_is_counted_per_user(ctx):
    _searches(ctx, 15, user="employee")
    _searches(ctx, 15, user="other")
    assert ctx["detect"]() == []


def test_new_thresholds_are_configurable(ctx, monkeypatch):
    _searches(ctx, 5)
    _view_attempts(ctx, MISSING_IDS[:2], reason="DOCUMENT_NOT_FOUND")
    assert ctx["detect"]() == []
    monkeypatch.setattr(settings, "alert_search_burst_threshold", 5)
    monkeypatch.setattr(settings, "alert_document_enumeration_threshold", 2)
    assert sorted(_rules(ctx["detect"]())) == sorted(
        [AlertRule.SEARCH_BURST, AlertRule.DOCUMENT_ENUMERATION]
    )


def test_end_to_end_real_searches_raise_search_burst(ctx):
    employee = ctx["headers"]("employee")
    for _ in range(30):
        ctx["client"].get("/documents/search_document?q=zz", headers=employee)
    alerts = (
        ctx["client"].get("/audit/alerts?rule=SEARCH_BURST", headers=ctx["headers"]("admin")).json()
    )
    assert [a["rule"] for a in alerts] == ["SEARCH_BURST"]
    assert alerts[0]["user_email"] == "employee@example.com"


# --- Non-regression: scenarios compared with the sprint2-ssi detector ----------------


def _repeat(env, action, count, *, user, result=AuditResult.FAILURE, resource_id=None, reason=None):
    env["add"](
        action,
        count,
        user=user,
        start=NOW - timedelta(minutes=3),
        step=timedelta(seconds=1),
        result=result,
        resource_type=ResourceType.DOCUMENT,
        resource_id=resource_id,
        details={"reason": reason} if reason else None,
    )


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("login_failures", {AlertRule.LOGIN_BRUTE_FORCE}),
        ("admin_functional_failures", set()),
        ("missing_consecutive_ids", {AlertRule.DOCUMENT_ENUMERATION}),
        ("fast_searches", {AlertRule.SEARCH_BURST}),
        ("spread_searches", set()),
        ("anonymous_rejected_tokens", {AlertRule.TOKEN_ABUSE}),
        ("admin_route_denials", {AlertRule.ADMIN_FEATURE_PROBING}),
        ("refused_document_creations", {AlertRule.ADMIN_FEATURE_PROBING}),
        ("expired_sessions", set()),
        ("department_refusals", {AlertRule.CROSS_DEPARTMENT_PROBING}),
    ],
)
def test_compared_scenarios_do_not_regress(ctx, scenario, expected):
    A = AuditAction
    if scenario == "login_failures":
        _repeat(ctx, A.LOGIN, 5, user="employee")
    elif scenario == "admin_functional_failures":
        _repeat(ctx, A.DOCUMENT_UPDATE, 5, user="admin", resource_id=99)
        _repeat(ctx, A.DOCUMENT_VIEW, 1, user="admin", resource_id=99)
    elif scenario == "missing_consecutive_ids":
        _view_attempts(ctx, [10, 11, 12, 13])
    elif scenario == "fast_searches":
        _searches(ctx, 30)
    elif scenario == "spread_searches":
        _searches(ctx, 30, step=timedelta(seconds=6))
    elif scenario == "anonymous_rejected_tokens":
        _repeat(ctx, A.AUTHENTICATION_FAILED, 10, user=None)
    elif scenario == "admin_route_denials":
        _repeat(ctx, A.ACCESS_DENIED, 3, user="employee")
    elif scenario == "refused_document_creations":
        _repeat(ctx, A.DOCUMENT_CREATE, 3, user="employee", reason="ROLE_FORBIDDEN")
    elif scenario == "expired_sessions":
        _repeat(ctx, A.SESSION_EXPIRED, 50, user=None)
    elif scenario == "department_refusals":
        _view_attempts(ctx, [2, 3, 4], reason="DEPARTMENT_FORBIDDEN")
    assert set(_rules(ctx["detect"]())) == expected


# --- Configuration -----------------------------------------------------------------


def _settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-with-enough-length",
        **overrides,
    )


def test_default_alert_settings_are_valid():
    config = _settings()
    assert config.alert_spike_baseline_days * 1440 // config.alert_spike_window_minutes == 1008


@pytest.mark.parametrize(
    "field",
    [
        name
        for name in Settings.model_fields
        if name.startswith("alert_") and name != "alert_spike_factor"
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_alert_integers_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


@pytest.mark.parametrize("value", [0.99, 0, -5])
def test_spike_factor_must_be_at_least_one(value):
    with pytest.raises(ValidationError):
        _settings(alert_spike_factor=value)
    assert _settings(alert_spike_factor=1).alert_spike_factor == 1


def test_lookback_is_capped_at_168_hours():
    assert _settings(alert_lookback_hours=168).alert_lookback_hours == 168
    with pytest.raises(ValidationError):
        _settings(alert_lookback_hours=169)


def test_baseline_must_split_into_whole_windows():
    assert _settings(alert_spike_window_minutes=15).alert_spike_window_minutes == 15
    with pytest.raises(ValidationError, match="divisible"):
        _settings(alert_spike_window_minutes=11)


def test_invalid_environment_value_is_rejected(monkeypatch):
    monkeypatch.setenv("ALERT_SPIKE_MIN_ACTIVE_WINDOWS", "0")
    with pytest.raises(ValidationError):
        _settings()
