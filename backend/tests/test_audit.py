# Tests du journal d'audit et de la traçabilité

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus


@pytest.fixture
def audit_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        front = Department(name="Front Office")
        db.add(front)
        db.flush()
        admin = User(
            email="admin@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=front.id,
        )
        employee = User(
            email="employee@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=front.id,
        )
        db.add_all([admin, employee])
        db.commit()
        ids = {"admin": admin.id, "employee": employee.id}

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def headers(user_key):
        with TestingSessionLocal() as db:
            user = db.get(User, ids[user_key])
            token = create_access_token(subject=str(user.id), role=user.role.value)
        return {"Authorization": f"Bearer {token}"}

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "headers": headers,
                "ids": ids,
                "session_factory": TestingSessionLocal,
            }
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed_login_events(env):
    client = env["client"]
    client.post("/auth/login", json={"email": "admin@example.com", "password": "Password123"})
    client.post("/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    client.post("/auth/login", json={"email": "employee@example.com", "password": "Password123"})


def test_audit_requires_admin(audit_context):
    env = audit_context
    assert env["client"].get("/audit").status_code == 401
    employee = env["headers"]("employee")
    assert env["client"].get("/audit", headers=employee).status_code == 403


def test_audit_lists_events_with_metadata(audit_context):
    env = audit_context
    _seed_login_events(env)

    response = env["client"].get("/audit", headers=env["headers"]("admin"))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    first = body["items"][0]
    assert set(first) == {
        "id",
        "user_id",
        "user_email",
        "action",
        "resource_type",
        "resource_id",
        "result",
        "ip_address",
        "timestamp",
    }
    assert first["action"] == "LOGIN"
    assert first["resource_type"] == ResourceType.USER.value


def test_audit_filters_by_action_and_result(audit_context):
    env = audit_context
    _seed_login_events(env)
    auth = env["headers"]("admin")

    by_action = env["client"].get("/audit?action=LOGIN", headers=auth).json()
    assert by_action["total"] == 3

    success = env["client"].get("/audit?result=SUCCESS", headers=auth).json()
    assert success["total"] == 2
    assert all(item["result"] == "SUCCESS" for item in success["items"])

    failure = env["client"].get("/audit?result=FAILURE", headers=auth).json()
    assert failure["total"] == 1
    assert failure["items"][0]["result"] == "FAILURE"


def test_audit_filters_by_user_email(audit_context):
    env = audit_context
    _seed_login_events(env)
    auth = env["headers"]("admin")

    employee = env["client"].get("/audit?user_email=employee@example.com", headers=auth).json()
    assert employee["total"] == 1
    assert employee["items"][0]["user_email"] == "employee@example.com"

    admin = env["client"].get("/audit?user_email=admin@example.com", headers=auth).json()
    assert admin["total"] == 2


def test_audit_accepts_all_filter_values(audit_context):
    env = audit_context
    _seed_login_events(env)
    response = env["client"].get(
        "/audit?action=ALL&result=ALL&period=all",
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3


def test_audit_rejects_unknown_filter_values(audit_context):
    env = audit_context
    auth = env["headers"]("admin")
    assert env["client"].get("/audit?action=NOPE", headers=auth).status_code == 422
    assert env["client"].get("/audit?result=NOPE", headers=auth).status_code == 422
    assert env["client"].get("/audit?period=NOPE", headers=auth).status_code == 422


def test_audit_pagination_metadata(audit_context):
    env = audit_context
    _seed_login_events(env)
    auth = env["headers"]("admin")

    page_one = env["client"].get("/audit?page=1&page_size=2", headers=auth).json()
    assert page_one["total"] == 3
    assert page_one["pages"] == 2
    assert len(page_one["items"]) == 2
    assert page_one["has_next"] is True
    assert page_one["has_prev"] is False

    page_two = env["client"].get("/audit?page=2&page_size=2", headers=auth).json()
    assert len(page_two["items"]) == 1
    assert page_two["has_next"] is False
    assert page_two["has_prev"] is True


def test_audit_export_returns_csv_and_is_itself_audited(audit_context):
    env = audit_context
    _seed_login_events(env)
    auth = env["headers"]("admin")

    response = env["client"].get("/audit/export", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("timestamp,user_email,action,resource_type,resource_id,result")
    assert len(lines) == 4

    with_session = env["client"].get("/audit?action=EXPORT", headers=auth).json()
    assert with_session["total"] == 1
    assert with_session["items"][0]["resource_type"] == ResourceType.SESSION.value


def test_audit_export_requires_admin(audit_context):
    env = audit_context
    assert env["client"].get("/audit/export").status_code == 401
    assert env["client"].get("/audit/export", headers=env["headers"]("employee")).status_code == 403


def test_user_mutations_are_audited(audit_context):
    env = audit_context
    auth = env["headers"]("admin")
    employee_id = env["ids"]["employee"]

    assert (
        env["client"]
        .patch(f"/users/{employee_id}", headers=auth, json={"email": "renamed@example.com"})
        .status_code
        == 200
    )
    assert (
        env["client"]
        .post(f"/users/{employee_id}/status", headers=auth, json={"active": False})
        .status_code
        == 200
    )

    updated = env["client"].get("/audit?action=USER_UPDATE", headers=auth).json()
    assert updated["total"] == 1
    assert updated["items"][0]["result"] == "SUCCESS"
    assert updated["items"][0]["resource_id"] == employee_id

    status = env["client"].get("/audit?action=USER_STATUS_UPDATE", headers=auth).json()
    assert status["total"] == 1
    assert status["items"][0]["result"] == "SUCCESS"


REFUSAL_ACTIONS = {
    AuditAction.AUTHENTICATION_FAILED.value,
    AuditAction.TOKEN_MISSING.value,
    AuditAction.SESSION_EXPIRED.value,
    AuditAction.ACCESS_DENIED.value,
}


def _events(env, action=None):
    # A fresh session proves the event was committed despite the error response.
    with env["session_factory"]() as db:
        query = select(AuditLog)
        if action is not None:
            query = query.where(AuditLog.action == action.value)
        return db.scalars(query.order_by(AuditLog.id)).all()


def _assert_single_auth_failure(env, user_id=None, action=AuditAction.AUTHENTICATION_FAILED):
    events = _events(env)
    assert len(events) == 1
    event = events[0]
    assert event.action == action.value
    assert event.resource_type == ResourceType.SESSION.value
    assert event.result == AuditResult.FAILURE.value
    assert event.user_id == user_id
    assert event.resource_id is None
    assert event.ip_address is not None


def test_missing_token_is_audited_as_token_missing(audit_context):
    env = audit_context
    assert env["client"].get("/audit").status_code == 401
    _assert_single_auth_failure(env, action=AuditAction.TOKEN_MISSING)
    assert _events(env, AuditAction.AUTHENTICATION_FAILED) == []


def test_non_bearer_scheme_is_audited_as_token_missing(audit_context):
    env = audit_context
    response = env["client"].get("/audit", headers={"Authorization": "Basic YWRtaW46c2VjcmV0"})
    assert response.status_code == 401
    _assert_single_auth_failure(env, action=AuditAction.TOKEN_MISSING)


def test_invalid_token_is_audited(audit_context):
    env = audit_context
    response = env["client"].get("/audit", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    _assert_single_auth_failure(env)


def _expired_token(env, secret):
    return jwt.encode(
        {
            "sub": str(env["ids"]["admin"]),
            "role": "ADMIN",
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "jti": "expired-token-audit",
        },
        secret,
        algorithm=settings.jwt_algorithm,
    )


def test_expired_token_is_audited_as_session_expiry_anonymously(audit_context):
    env = audit_context
    token = _expired_token(env, settings.jwt_secret_key)
    response = env["client"].get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401

    events = _events(env)
    assert len(events) == 1
    assert events[0].action == AuditAction.SESSION_EXPIRED.value
    assert events[0].resource_type == ResourceType.SESSION.value
    assert events[0].result == AuditResult.FAILURE.value
    assert events[0].user_id is None
    assert _events(env, AuditAction.AUTHENTICATION_FAILED) == []


def test_expired_token_with_invalid_signature_is_an_authentication_failure(audit_context):
    env = audit_context
    token = _expired_token(env, "attacker-secret-not-the-real-one-0123456789")
    response = env["client"].get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    _assert_single_auth_failure(env, user_id=None)
    assert _events(env, AuditAction.SESSION_EXPIRED) == []


def test_revoked_token_is_audited_with_user(audit_context):
    env = audit_context
    employee = env["headers"]("employee")
    assert env["client"].post("/auth/logout", headers=employee).status_code == 200

    assert env["client"].get("/auth/me", headers=employee).status_code == 401

    failures = _events(env, AuditAction.AUTHENTICATION_FAILED)
    assert len(failures) == 1
    assert failures[0].user_id == env["ids"]["employee"]
    assert failures[0].resource_type == ResourceType.SESSION.value
    assert failures[0].result == AuditResult.FAILURE.value


def test_inactive_user_is_audited_with_user(audit_context):
    env = audit_context
    employee = env["headers"]("employee")
    with env["session_factory"]() as db:
        db.get(User, env["ids"]["employee"]).status = UserStatus.INACTIVE
        db.commit()

    assert env["client"].get("/auth/me", headers=employee).status_code == 401
    _assert_single_auth_failure(env, user_id=env["ids"]["employee"])


def test_employee_denied_audit_log_is_audited_once(audit_context):
    env = audit_context
    response = env["client"].get("/audit", headers=env["headers"]("employee"))
    assert response.status_code == 403

    events = _events(env)
    assert len(events) == 1
    event = events[0]
    assert event.action == AuditAction.ACCESS_DENIED.value
    assert event.resource_type == ResourceType.AUDIT_LOG.value
    assert event.result == AuditResult.FAILURE.value
    assert event.user_id == env["ids"]["employee"]


def test_employee_denied_user_update_is_audited_once(audit_context):
    env = audit_context
    admin_id = env["ids"]["admin"]
    response = env["client"].patch(
        f"/users/{admin_id}",
        headers=env["headers"]("employee"),
        json={"email": "hijack@example.com"},
    )
    assert response.status_code == 403

    events = _events(env)
    assert len(events) == 1
    assert events[0].action == AuditAction.ACCESS_DENIED.value
    assert events[0].resource_type == ResourceType.USER.value
    assert events[0].user_id == env["ids"]["employee"]
    assert _events(env, AuditAction.USER_UPDATE) == []


def test_refusal_events_never_store_token_or_secret(audit_context):
    env = audit_context
    employee = env["headers"]("employee")
    employee_token = employee["Authorization"].split(" ", 1)[1]
    forged_token = "forged.token.value"

    env["client"].get("/audit", headers=employee)
    env["client"].get("/audit", headers={"Authorization": f"Bearer {forged_token}"})
    env["client"].post("/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    env["client"].get("/audit", headers={"Authorization": "Basic YWRtaW46c2VjcmV0"})
    assert _events(env, AuditAction.TOKEN_MISSING)

    stored = " ".join(
        str(getattr(event, column.name))
        for event in _events(env)
        for column in AuditLog.__table__.columns
    )
    export = env["client"].get("/audit/export", headers=env["headers"]("admin")).text
    for secret in (
        employee_token,
        forged_token,
        "Bearer",
        "Basic",
        "YWRtaW46c2VjcmV0",
        "wrong",
        settings.jwt_secret_key,
    ):
        assert secret not in stored
        assert secret not in export


def test_authorized_requests_create_no_refusal_event(audit_context):
    env = audit_context
    admin = env["headers"]("admin")
    assert env["client"].get("/audit", headers=admin).status_code == 200
    assert env["client"].get("/users", headers=admin).status_code == 200
    assert env["client"].get("/departments", headers=env["headers"]("employee")).status_code == 200
    assert env["client"].get("/auth/me", headers=env["headers"]("employee")).status_code == 200

    assert not [event for event in _events(env) if event.action in REFUSAL_ACTIONS]


def test_audit_filters_on_refusal_actions(audit_context):
    env = audit_context
    env["client"].get("/audit", headers=env["headers"]("employee"))
    env["client"].get("/audit")
    env["client"].get("/audit", headers={"Authorization": "Bearer forged.token.value"})
    admin = env["headers"]("admin")

    denied = env["client"].get("/audit?action=ACCESS_DENIED", headers=admin)
    assert denied.status_code == 200
    assert denied.json()["total"] == 1

    for action in ("AUTHENTICATION_FAILED", "TOKEN_MISSING"):
        response = env["client"].get(f"/audit?action={action}", headers=admin)
        assert response.status_code == 200
        assert response.json()["total"] == 1
