import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        department = Department(name="Test Department")
        db.add(department)
        db.flush()

        user = User(
            email="auth.test@example.com",
            password_hash=hash_password("TestPassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=department.id,
        )
        db.add(user)
        db.commit()

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_login_success(client):
    response = client.post(
        "/auth/login",
        json={
            "email": "auth.test@example.com",
            "password": "TestPassword123",
        },
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]


def test_login_invalid_password(client):
    response = client.post(
        "/auth/login",
        json={
            "email": "auth.test@example.com",
            "password": "WrongPassword123",
        },
    )

    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_me_returns_authenticated_user(client):
    login = client.post(
        "/auth/login",
        json={
            "email": "auth.test@example.com",
            "password": "TestPassword123",
        },
    )
    token = login.json()["access_token"]

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "auth.test@example.com"
    assert response.json()["role"] == "ADMIN"
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["department_id"] == 1
    assert response.json()["department_name"] == "Test Department"


def test_logout_revokes_token(client):
    login = client.post(
        "/auth/login",
        json={
            "email": "auth.test@example.com",
            "password": "TestPassword123",
        },
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    logout = client.post("/auth/logout", headers=headers)

    assert logout.status_code == 200

    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 401
