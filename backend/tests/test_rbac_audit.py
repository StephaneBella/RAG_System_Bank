# RBAC and audit scenarios of the SSI sprint (RBAC-01, 03, 04, 08, 09, 12, 13 and R1 alerts)

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
from app.models.audit_log import AuditAction, AuditLog, AuditResult
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus

EMPLOYEE_EMAIL = "employee@example.com"
GOOD_PASSWORD = "Password123"


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
            password_hash=hash_password(GOOD_PASSWORD),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=front.id,
        )
        employee = User(
            email=EMPLOYEE_EMAIL,
            password_hash=hash_password(GOOD_PASSWORD),
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


def audit_rows(ctx):
    with ctx.session_factory() as db:
        return list(db.scalars(select(AuditLog).order_by(AuditLog.id)).all())


def row_text(row):
    values = (row.user_id, row.action, row.resource_type, row.resource_id, row.result)
    return " ".join(str(value) for value in (*values, row.ip_address))


def login(ctx, email, password):
    return ctx.client.post("/auth/login", json={"email": email, "password": password})


@pytest.mark.parametrize(
    "path",
    ["/auth/me", "/documents/get_all", "/users", "/audit", "/audit/alerts"],
)
def test_rbac_01_protected_routes_require_a_token(ctx, path):
    assert ctx.client.get(path).status_code == 401


def test_rbac_03_token_is_rejected_after_logout(ctx):
    headers = ctx.auth("employee")
    assert ctx.client.post("/auth/logout", headers=headers).status_code == 200

    assert ctx.client.get("/auth/me", headers=headers).status_code == 401


def test_rbac_04_deactivated_user_is_rejected_with_a_valid_token(ctx):
    headers = ctx.auth("employee")
    with ctx.session_factory() as db:
        user = db.get(User, ctx.ids["employee"])
        user.status = UserStatus.INACTIVE
        db.commit()

    assert ctx.client.get("/auth/me", headers=headers).status_code == 401


def test_rbac_08_employee_cannot_delete_a_document_and_the_denial_is_logged(ctx):
    response = ctx.client.delete("/documents/delete/1", headers=ctx.auth("employee"))

    assert response.status_code == 403
    rows = [row for row in audit_rows(ctx) if row.action == AuditAction.DOCUMENT_DELETE.value]
    assert len(rows) == 1
    assert rows[0].result == AuditResult.FAILURE.value
    assert rows[0].user_id == ctx.ids["employee"]


@pytest.mark.parametrize("path", ["/audit", "/audit/alerts", "/users"])
def test_rbac_09_employee_cannot_use_admin_routes(ctx, path):
    assert ctx.client.get(path, headers=ctx.auth("employee")).status_code == 403


@pytest.mark.parametrize("path", ["/audit", "/audit/alerts", "/users"])
def test_rbac_09_admin_can_use_admin_routes(ctx, path):
    assert ctx.client.get(path, headers=ctx.auth("admin")).status_code == 200


def test_rbac_12_login_errors_do_not_reveal_which_accounts_exist(ctx):
    wrong_password = login(ctx, EMPLOYEE_EMAIL, "Wrong-Pass-123")
    unknown_email = login(ctx, "nobody@example.com", "Wrong-Pass-123")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    failures = [row for row in audit_rows(ctx) if row.result == AuditResult.FAILURE.value]
    assert len(failures) == 2
    assert {row.user_id for row in failures} == {ctx.ids["employee"], None}


def test_rbac_13_audit_log_never_contains_secrets(ctx):
    wrong_secret = "Sup3r-Secret-Pass!"
    login(ctx, EMPLOYEE_EMAIL, wrong_secret)
    success = login(ctx, EMPLOYEE_EMAIL, GOOD_PASSWORD)
    assert success.status_code == 200
    token = success.json()["access_token"]

    rows = audit_rows(ctx)
    assert rows
    for row in rows:
        text = row_text(row)
        assert wrong_secret not in text
        assert GOOD_PASSWORD not in text
        assert token not in text


def test_repeated_failed_logins_raise_an_r1_alert_for_the_admin(ctx):
    for _ in range(5):
        assert login(ctx, EMPLOYEE_EMAIL, "Wrong-Pass-123").status_code == 401

    response = ctx.client.get("/audit/alerts", headers=ctx.auth("admin"))

    assert response.status_code == 200
    assert "Wrong-Pass-123" not in response.text
    employee_id = str(ctx.ids["employee"])
    assert any(
        alert["rule"] == "R1"
        and alert["subject_type"] == "user"
        and alert["subject"] == employee_id
        for alert in response.json()
    )


def test_alerts_are_empty_without_suspicious_activity(ctx):
    response = ctx.client.get("/audit/alerts", headers=ctx.auth("admin"))

    assert response.status_code == 200
    assert response.json() == []
