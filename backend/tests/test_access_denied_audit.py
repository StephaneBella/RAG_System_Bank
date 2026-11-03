# Refused access to admin routes must leave a trace in the audit log (SEC-01)

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus

EMPLOYEE_EMAIL = "employee@example.com"
PASSWORD = "Password123"


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with session_factory() as db:
        front = Department(name="Front Office")
        db.add(front)
        db.flush()
        admin = User(
            email="admin@example.com",
            password_hash=hash_password(PASSWORD),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=front.id,
        )
        employee = User(
            email=EMPLOYEE_EMAIL,
            password_hash=hash_password(PASSWORD),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=front.id,
        )
        db.add_all([admin, employee])
        db.commit()
        ids = {"admin": admin.id, "employee": employee.id}

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    def auth(user_key):
        with session_factory() as db:
            user = db.get(User, ids[user_key])
            token = create_access_token(subject=str(user.id), role=user.role.value)
        return {"Authorization": f"Bearer {token}"}

    yield SimpleNamespace(
        client=TestClient(app),
        auth=auth,
        session_factory=session_factory,
        ids=ids,
    )

    app.dependency_overrides.clear()
    engine.dispose()


def denied_rows(ctx):
    with ctx.session_factory() as db:
        query = select(AuditLog).where(AuditLog.action == AuditAction.ACCESS_DENIED.value)
        return list(db.scalars(query.order_by(AuditLog.id)).all())


@pytest.mark.parametrize(
    ("path", "resource_type"),
    [
        ("/audit", ResourceType.SESSION),
        ("/audit/alerts", ResourceType.SESSION),
        ("/users", ResourceType.USER),
    ],
)
def test_employee_denied_on_admin_route_is_logged(ctx, path, resource_type):
    response = ctx.client.get(path, headers=ctx.auth("employee"))

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
    rows = denied_rows(ctx)
    assert len(rows) == 1
    assert rows[0].user_id == ctx.ids["employee"]
    assert rows[0].resource_type == resource_type.value
    assert rows[0].result == AuditResult.FAILURE.value


@pytest.mark.parametrize("path", ["/audit", "/audit/alerts", "/users"])
def test_admin_access_is_not_logged_as_denied(ctx, path):
    assert ctx.client.get(path, headers=ctx.auth("admin")).status_code == 200

    assert denied_rows(ctx) == []


def test_unauthenticated_request_is_rejected_without_denied_event(ctx):
    assert ctx.client.get("/audit").status_code == 401

    assert denied_rows(ctx) == []


def test_admin_can_find_the_denied_attempt_in_the_audit_log(ctx):
    ctx.client.get("/users", headers=ctx.auth("employee"))

    response = ctx.client.get(
        "/audit?action=ACCESS_DENIED&result=FAILURE",
        headers=ctx.auth("admin"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "ACCESS_DENIED"
    assert body["items"][0]["user_email"] == EMPLOYEE_EMAIL


def test_repeated_denied_attempts_raise_an_r2_alert(ctx):
    for _ in range(5):
        assert ctx.client.get("/users", headers=ctx.auth("employee")).status_code == 403

    response = ctx.client.get("/audit/alerts", headers=ctx.auth("admin"))

    assert response.status_code == 200
    employee_id = str(ctx.ids["employee"])
    assert any(
        alert["rule"] == "R2" and alert["subject"] == employee_id for alert in response.json()
    )
