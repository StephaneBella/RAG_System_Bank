from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus
from app.models.user_invitation import UserInvitation
from app.services.invitation_service import accept_invitation


@pytest.fixture
def invitation_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as db:
        department = Department(name="Invitation Department")
        db.add(department)
        db.commit()
        department_id = department.id

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, TestingSessionLocal, department_id
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create_admin(session_factory):
    with session_factory() as db:
        department = db.scalar(select(Department).where(Department.name == "Invitation Department"))
        admin = User(
            email="invitation.admin@example.com",
            password_hash=hash_password("AdminPassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=department.id,
        )
        db.add(admin)
        db.commit()


def admin_headers(client):
    response = client.post(
        "/auth/login",
        json={"email": "invitation.admin@example.com", "password": "AdminPassword123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_invitation_lifecycle_and_single_use(invitation_context):
    client, session_factory, department_id = invitation_context
    create_admin(session_factory)
    headers = admin_headers(client)

    response = client.post(
        "/users/invitations",
        headers=headers,
        json={
            "email": "new.employee@example.com",
            "role": "EMPLOYEE",
            "department_id": department_id,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    token = payload["invitation_token"]

    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "new.employee@example.com"))
        assert user.status == UserStatus.INACTIVE
        assert user.password_hash != token
        invitation = db.scalar(select(UserInvitation).where(UserInvitation.user_id == user.id))
        assert invitation.token_hash == sha256(token.encode()).hexdigest()
        assert invitation.token_hash != token

    accepted = client.post(
        "/auth/accept-invitation",
        json={"token": token, "password": "NewEmployeePass123"},
    )
    assert accepted.status_code == 200

    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "new.employee@example.com"))
        assert user.status == UserStatus.ACTIVE
        assert verify_password("NewEmployeePass123", user.password_hash)
        invitation = db.scalar(select(UserInvitation).where(UserInvitation.user_id == user.id))
        assert invitation.used_at is not None

    repeated = client.post(
        "/auth/accept-invitation",
        json={"token": token, "password": "AnotherPassword123"},
    )
    assert repeated.status_code == 400


def test_invitation_rejects_invalid_token(invitation_context):
    client, _, _ = invitation_context
    response = client.post(
        "/auth/accept-invitation",
        json={"token": "x" * 32, "password": "ValidPassword123"},
    )
    assert response.status_code == 400


def test_invitation_rejects_expired_token(invitation_context):
    _, session_factory, department_id = invitation_context
    with session_factory() as db:
        user = User(
            email="expired.employee@example.com",
            password_hash=hash_password("TemporaryPassword123"),
            status=UserStatus.INACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=department_id,
        )
        db.add(user)
        db.flush()
        token = "expired-token-for-test"
        invitation = UserInvitation(
            user_id=user.id,
            token_hash=sha256(token.encode()).hexdigest(),
            created_at=datetime.now(UTC) - timedelta(hours=25),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(invitation)
        db.commit()

        with pytest.raises(ValueError, match="expired"):
            accept_invitation(db, token, "NewEmployeePass123")


def test_invitation_rejects_duplicate_email(invitation_context):
    client, session_factory, department_id = invitation_context
    create_admin(session_factory)
    headers = admin_headers(client)
    payload = {
        "email": "duplicate.employee@example.com",
        "role": "EMPLOYEE",
        "department_id": department_id,
    }
    first = client.post("/users/invitations", headers=headers, json=payload)
    assert first.status_code == 201
    second = client.post("/users/invitations", headers=headers, json=payload)
    assert second.status_code == 409


def test_invitation_requires_admin(invitation_context):
    client, _, department_id = invitation_context
    response = client.post(
        "/users/invitations",
        json={
            "email": "unauthorized.employee@example.com",
            "role": "EMPLOYEE",
            "department_id": department_id,
        },
    )
    assert response.status_code == 401


def test_employee_cannot_create_invitation(invitation_context):
    client, session_factory, department_id = invitation_context

    with session_factory() as db:
        employee = User(
            email="invitation.employee@example.com",
            password_hash=hash_password("EmployeePassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=department_id,
        )
        db.add(employee)
        db.commit()

    response = client.post(
        "/auth/login",
        json={
            "email": "invitation.employee@example.com",
            "password": "EmployeePassword123",
        },
    )
    assert response.status_code == 200
    employee_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    invitation_response = client.post(
        "/users/invitations",
        headers=employee_headers,
        json={
            "email": "unauthorized.invitee@example.com",
            "role": "EMPLOYEE",
            "department_id": department_id,
        },
    )

    assert invitation_response.status_code == 403
