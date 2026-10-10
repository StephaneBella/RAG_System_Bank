from datetime import UTC, datetime

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
from app.models.document import Document
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
        dept_a = Department(name="Dept A")
        dept_b = Department(name="Dept B")
        db.add_all([dept_a, dept_b])
        db.flush()

        db.add_all(
            [
                User(
                    email="admin@example.com",
                    password_hash=hash_password("Password123"),
                    status=UserStatus.ACTIVE,
                    role=UserRole.ADMIN,
                    department_id=dept_a.id,
                ),
                User(
                    email="employee@example.com",
                    password_hash=hash_password("Password123"),
                    status=UserStatus.ACTIVE,
                    role=UserRole.EMPLOYEE,
                    department_id=dept_a.id,
                ),
            ]
        )
        db.flush()

        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        for i in range(5):
            db.add(
                Document(
                    title=f"A{i}",
                    department_id=dept_a.id,
                    file_path=f"data/documents/a{i}.pdf",
                    content="alpha document",
                    created_at=now,
                    updated_at=now,
                )
            )
        db.add(
            Document(
                title="B0",
                department_id=dept_b.id,
                file_path="data/documents/b0.pdf",
                content="beta document",
                created_at=now,
                updated_at=now,
            )
        )
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


def _headers(client, email):
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "Password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_pagination_metadata(client):
    headers = _headers(client, "admin@example.com")
    response = client.get("/documents/get_all?page=1&page_size=2", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 6
    assert body["pages"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2
    assert body["has_next"] is True
    assert body["has_prev"] is False


def test_pagination_last_page(client):
    headers = _headers(client, "admin@example.com")
    body = client.get("/documents/get_all?page=3&page_size=2", headers=headers).json()

    assert len(body["items"]) == 2
    assert body["has_next"] is False
    assert body["has_prev"] is True


def test_invalid_pagination_params_rejected(client):
    headers = _headers(client, "admin@example.com")

    assert client.get("/documents/get_all?page_size=101", headers=headers).status_code == 422
    assert client.get("/documents/get_all?page=0", headers=headers).status_code == 422


def test_employee_only_sees_own_department(client):
    headers = _headers(client, "employee@example.com")
    body = client.get("/documents/get_all", headers=headers).json()

    assert body["total"] == 5
    assert len({item["department_id"] for item in body["items"]}) == 1


def test_search_respects_department(client):
    employee = _headers(client, "employee@example.com")
    admin = _headers(client, "admin@example.com")

    assert client.get("/documents/search_document?q=beta", headers=employee).json()["total"] == 0
    assert client.get("/documents/search_document?q=beta", headers=admin).json()["total"] == 1


def test_sorting_by_title_ascending(client):
    headers = _headers(client, "admin@example.com")
    body = client.get(
        "/documents/get_all?sort_by=title&order=asc&page_size=100",
        headers=headers,
    ).json()

    assert [item["title"] for item in body["items"]] == ["A0", "A1", "A2", "A3", "A4", "B0"]


def test_invalid_sort_field_rejected(client):
    headers = _headers(client, "admin@example.com")

    assert client.get("/documents/get_all?sort_by=password", headers=headers).status_code == 400


def test_employee_cannot_create_document(client):
    headers = _headers(client, "employee@example.com")
    response = client.post(
        "/documents/add",
        data={"title": "New", "department_id": 1},
        files={"file": ("x.txt", b"hello", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 403
