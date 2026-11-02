from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table on Base)
from app.db.base import Base
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.services.audit_detection import (
    DetectionThresholds,
    detect_suspicious_activity,
    longest_consecutive_run,
)

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


@pytest.fixture
def db():
    """Self-contained in-memory database: no PostgreSQL needed for these tests."""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_event(
    db: Session,
    *,
    action: AuditAction,
    result: AuditResult,
    user_id: int | None = None,
    resource_id: int | None = None,
    ip: str | None = None,
    minutes_ago: float = 0.0,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action.value,
            resource_type=ResourceType.DOCUMENT.value,
            resource_id=resource_id,
            result=result.value,
            timestamp=NOW - timedelta(minutes=minutes_ago),
            ip_address=ip,
        )
    )
    db.commit()


def alerts_for(db: Session, **kwargs):
    return detect_suspicious_activity(db, now=NOW, **kwargs)


def rules(alerts) -> set[str]:
    return {alert.rule for alert in alerts}


def test_no_events_means_no_alerts(db):
    assert alerts_for(db) == []


@pytest.mark.parametrize(("attempts", "expected"), [(4, set()), (5, {"R1"})])
def test_r1_failed_logins_from_one_ip(db, attempts, expected):
    for _ in range(attempts):
        add_event(db, action=AuditAction.LOGIN, result=AuditResult.FAILURE, ip="10.0.0.9")

    alerts = alerts_for(db)

    assert rules(alerts) == expected
    if expected:
        assert alerts[0].subject_type == "ip"
        assert alerts[0].subject == "10.0.0.9"
        assert alerts[0].count == attempts


def test_r1_failed_logins_against_one_account(db):
    for index in range(5):
        add_event(
            db,
            action=AuditAction.LOGIN,
            result=AuditResult.FAILURE,
            user_id=7,
            ip=f"10.0.0.{index}",
        )

    alerts = alerts_for(db)

    assert [(a.rule, a.subject_type, a.subject) for a in alerts] == [("R1", "user", "7")]


def test_r1_ignores_events_outside_the_window(db):
    for _ in range(5):
        add_event(
            db,
            action=AuditAction.LOGIN,
            result=AuditResult.FAILURE,
            ip="10.0.0.9",
            minutes_ago=11,
        )

    assert alerts_for(db) == []


def test_r1_ignores_successful_logins(db):
    for _ in range(10):
        add_event(db, action=AuditAction.LOGIN, result=AuditResult.SUCCESS, ip="10.0.0.9")

    assert alerts_for(db) == []


@pytest.mark.parametrize(("refusals", "expected"), [(4, set()), (5, {"R2"})])
def test_r2_refused_document_operations(db, refusals, expected):
    for _ in range(refusals):
        add_event(
            db,
            action=AuditAction.DOCUMENT_VIEW,
            result=AuditResult.FAILURE,
            user_id=3,
            resource_id=42,
        )

    assert rules(alerts_for(db)) == expected


def test_r2_does_not_count_successful_operations(db):
    for _ in range(20):
        add_event(db, action=AuditAction.DOCUMENT_VIEW, result=AuditResult.SUCCESS, user_id=3)

    assert alerts_for(db) == []


@pytest.mark.parametrize(("searches", "expected"), [(29, set()), (30, {"R3"})])
def test_r3_search_peak_in_the_last_minute(db, searches, expected):
    for _ in range(searches):
        add_event(
            db,
            action=AuditAction.DOCUMENT_SEARCH,
            result=AuditResult.SUCCESS,
            user_id=5,
            minutes_ago=0.5,
        )

    assert rules(alerts_for(db)) == expected


def test_r3_ignores_searches_spread_over_time(db):
    for index in range(30):
        add_event(
            db,
            action=AuditAction.DOCUMENT_SEARCH,
            result=AuditResult.SUCCESS,
            user_id=5,
            minutes_ago=index * 0.3,
        )

    assert alerts_for(db) == []


def test_r4_detects_consecutive_document_ids(db):
    for resource_id in (10, 11, 12, 13):
        add_event(
            db,
            action=AuditAction.DOCUMENT_VIEW,
            result=AuditResult.FAILURE,
            user_id=9,
            resource_id=resource_id,
        )

    alerts = alerts_for(db)

    assert rules(alerts) == {"R4"}
    assert alerts[0].count == 4


def test_r4_ignores_scattered_document_ids(db):
    for resource_id in (10, 20, 30, 40):
        add_event(
            db,
            action=AuditAction.DOCUMENT_VIEW,
            result=AuditResult.FAILURE,
            user_id=9,
            resource_id=resource_id,
        )

    assert alerts_for(db) == []


def test_thresholds_are_configurable(db):
    for _ in range(2):
        add_event(db, action=AuditAction.LOGIN, result=AuditResult.FAILURE, ip="10.0.0.9")

    assert alerts_for(db) == []
    assert rules(alerts_for(db, thresholds=DetectionThresholds(failed_logins=2))) == {"R1"}


def test_alerts_expose_no_sensitive_content(db):
    for _ in range(5):
        add_event(db, action=AuditAction.LOGIN, result=AuditResult.FAILURE, ip="10.0.0.9")

    for alert in alerts_for(db):
        text = " ".join(str(value) for value in vars(alert).values()).lower()
        assert "password" not in text
        assert "token" not in text


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], 0), ([5], 1), ([1, 2, 3], 3), ([1, 2, 4, 5, 6], 3), ([9, 9, 9], 1), ([3, 1, 2], 3)],
)
def test_longest_consecutive_run(values, expected):
    assert longest_consecutive_run(values) == expected
