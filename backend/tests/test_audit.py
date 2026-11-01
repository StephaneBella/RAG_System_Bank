# Tests du journal d'audit et de la traçabilité

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.audit_log import ResourceType
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
            yield {"client": client, "headers": headers, "ids": ids}
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
