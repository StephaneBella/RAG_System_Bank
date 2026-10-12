from pathlib import Path

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
from app.services import document_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    documents_dir = tmp_path / "documents"
    archive_dir = tmp_path / "archive"
    documents_dir.mkdir()

    monkeypatch.setattr(document_service, "DOCUMENTS_DIR", documents_dir)
    monkeypatch.setattr(document_service, "ARCHIVE_DIR", archive_dir)

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
        front_office = Department(name="Front Office")
        compliance = Department(name="Compliance/LBC-FT")
        db.add_all([front_office, compliance])
        db.flush()
        front_office_id = front_office.id

        db.add_all(
            [
                User(
                    email="admin@example.com",
                    password_hash=hash_password("Password123"),
                    status=UserStatus.ACTIVE,
                    role=UserRole.ADMIN,
                    department_id=front_office.id,
                ),
                User(
                    email="employee@example.com",
                    password_hash=hash_password("Password123"),
                    status=UserStatus.ACTIVE,
                    role=UserRole.EMPLOYEE,
                    department_id=front_office.id,
                ),
            ]
        )
        db.commit()

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    for file_name in (
        "public__front_office__alpha_report.pdf",
        "private__front_office__beta_notes.pdf",
        "public__compliance_lbc_ft__secret_report.pdf",
    ):
        (documents_dir / file_name).write_bytes(b"%PDF-1.4 test")

    def fake_extract_text(file_path: str) -> str:
        return Path(file_path).stem

    monkeypatch.setattr("app.api.routes.documents.extract_text", fake_extract_text)

    with TestClient(app) as test_client:
        yield {
            "client": test_client,
            "documents_dir": documents_dir,
            "archive_dir": archive_dir,
            "front_office_id": front_office_id,
        }

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _headers(client, email):
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "Password123"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _ids(client):
    headers = _headers(client, "admin@example.com")
    body = client.get("/documents/get_all", headers=headers).json()
    return {item["title"]: item["id"] for item in body["items"]}


def test_seeded_folder_auto_indexed(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")
    body = client.get("/documents/get_all", headers=headers).json()

    assert body["total"] == 3
    ids = [item["id"] for item in body["items"]]
    assert len(ids) == len(set(ids))
    assert (env["documents_dir"] / ".documents.json").exists()


def test_admin_pagination_metadata(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    page_one = client.get("/documents/get_all?page=1&page_size=2", headers=headers).json()
    assert page_one["total"] == 3
    assert page_one["pages"] == 2
    assert page_one["page_size"] == 2
    assert len(page_one["items"]) == 2
    assert page_one["has_next"] is True
    assert page_one["has_prev"] is False

    page_two = client.get("/documents/get_all?page=2&page_size=2", headers=headers).json()
    assert len(page_two["items"]) == 1
    assert page_two["has_next"] is False
    assert page_two["has_prev"] is True


def test_invalid_pagination_params_rejected(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    assert client.get("/documents/get_all?page_size=101", headers=headers).status_code == 422
    assert client.get("/documents/get_all?page=0", headers=headers).status_code == 422


def test_invalid_sort_field_rejected(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    assert client.get("/documents/get_all?sort_by=content", headers=headers).status_code == 400


def test_employee_sees_only_own_department(env):
    client = env["client"]
    headers = _headers(client, "employee@example.com")
    body = client.get("/documents/get_all", headers=headers).json()

    assert body["total"] == 2
    titles = {item["title"] for item in body["items"]}
    assert "alpha_report" in titles
    assert "beta_notes" in titles
    assert "secret_report" not in titles
    assert {item["department_name"] for item in body["items"]} == {"Front Office"}


def test_search_respects_scope(env):
    client = env["client"]
    employee = _headers(client, "employee@example.com")
    admin = _headers(client, "admin@example.com")

    assert client.get("/documents/search_document?q=secret", headers=employee).json()["total"] == 0
    assert client.get("/documents/search_document?q=beta", headers=employee).json()["total"] == 1
    assert client.get("/documents/search_document?q=secret", headers=admin).json()["total"] == 1


def test_add_document(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    response = client.post(
        "/documents/add",
        data={
            "title": "new contract",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("contract.pdf", b"%PDF-1.4 new", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "new_contract"
    assert body["department_name"] == "Front Office"
    assert body["file_path"] == "data/documents/public__front_office__new_contract.pdf"
    stored_name = Path(body["file_path"]).name
    assert (env["documents_dir"] / stored_name).exists()


def test_add_duplicate_conflict(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    response = client.post(
        "/documents/add",
        data={
            "title": "alpha report",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("alpha.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 409


def test_employee_cannot_create(env):
    client = env["client"]
    headers = _headers(client, "employee@example.com")

    response = client.post(
        "/documents/add",
        data={
            "title": "blocked",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("blocked.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 403


def test_add_unsupported_extension(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    response = client.post(
        "/documents/add",
        data={
            "title": "bad file",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("bad.txt", b"hello", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 400


def test_update_archives_and_keeps_id(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")
    document_id = _ids(client)["alpha_report"]

    response = client.put(
        f"/documents/update/{document_id}",
        data={
            "title": "renamed report",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("renamed.pdf", b"%PDF-1.4 updated", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["title"] == "renamed_report"
    assert (env["archive_dir"] / "public__front_office__alpha_report.pdf").exists()
    assert not (env["documents_dir"] / "public__front_office__alpha_report.pdf").exists()
    assert (env["documents_dir"] / "public__front_office__renamed_report.pdf").exists()


def test_update_missing_document_404(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")

    response = client.put(
        "/documents/update/9999",
        data={
            "title": "nothing",
            "department_id": env["front_office_id"],
            "visibility": "public",
        },
        files={"file": ("nothing.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 404


def test_delete_removes_file_and_entry(env):
    client = env["client"]
    headers = _headers(client, "admin@example.com")
    document_id = _ids(client)["beta_notes"]

    response = client.delete(f"/documents/delete/{document_id}", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"detail": "Document deleted successfully"}
    assert not (env["documents_dir"] / "private__front_office__beta_notes.pdf").exists()
    assert client.get("/documents/get_all", headers=headers).json()["total"] == 2

    repeat = client.delete(f"/documents/delete/{document_id}", headers=headers)
    assert repeat.status_code == 404