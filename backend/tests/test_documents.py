from datetime import UTC, datetime
from pathlib import Path

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
from app.services import document_service


@pytest.fixture
def document_context(tmp_path, monkeypatch):
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
        bind=engine, autoflush=False, autocommit=False
    )
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        front_office = Department(name="Front Office")
        compliance = Department(name="Compliance/LBC-FT")
        db.add_all([front_office, compliance])
        db.flush()

        admin = User(
            email="admin@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=front_office.id,
        )
        employee_a = User(
            email="employee@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=front_office.id,
        )
        employee_b = User(
            email="employee.b@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=compliance.id,
        )
        db.add_all([admin, employee_a, employee_b])
        db.commit()

        ids = {
            "admin": admin.id,
            "employee_a": employee_a.id,
            "employee_b": employee_b.id,
            "front_office": front_office.id,
            "compliance": compliance.id,
        }

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.state.testing_session_factory = TestingSessionLocal

    seeded_files = (
        "public__front_office__alpha_report.pdf",
        "private__front_office__beta_notes.pdf",
        "public__compliance_lbc_ft__secret_report.pdf",
        "private__compliance_lbc_ft__confidential_report.pdf",
    )
    for file_name in seeded_files:
        (documents_dir / file_name).write_bytes(b"%PDF-1.4 test")

    def fake_extract_text(file_path: str) -> str:
        return Path(file_path).stem

    monkeypatch.setattr("app.api.routes.documents.extract_text", fake_extract_text)

    def headers(user_key):
        with TestingSessionLocal() as db:
            user = db.get(User, ids[user_key])
            token = create_access_token(
                subject=str(user.id),
                role=user.role.value,
            )
        return {"Authorization": f"Bearer {token}"}

    try:
        with TestClient(app) as client:
            yield {
                "client": client,
                "headers": headers,
                "ids": ids,
                "documents_dir": documents_dir,
                "archive_dir": archive_dir,
                "session_factory": TestingSessionLocal,
            }
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "testing_session_factory"):
            del app.state.testing_session_factory
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _document_ids(env):
    client = env["client"]
    body = client.get(
        "/documents/get_all", headers=env["headers"]("admin")
    ).json()
    return {item["title"]: item["id"] for item in body["items"]}


def _audit_event(env, action, user_id=None, resource_id=None):
    with env["session_factory"]() as db:
        query = select(AuditLog).where(AuditLog.action == action.value)
        if user_id is not None:
            query = query.where(AuditLog.user_id == user_id)
        if resource_id is not None:
            query = query.where(AuditLog.resource_id == resource_id)
        return db.scalar(query.order_by(AuditLog.id.desc()))


def test_seeded_folder_is_indexed_and_ids_are_unique(document_context):
    env = document_context
    response = env["client"].get(
        "/documents/get_all", headers=env["headers"]("admin")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    ids = [item["id"] for item in body["items"]]
    assert len(ids) == len(set(ids))
    assert (env["documents_dir"] / ".documents.json").exists()


def test_admin_pagination_metadata(document_context):
    env = document_context
    headers = env["headers"]("admin")
    page_one = env["client"].get(
        "/documents/get_all?page=1&page_size=2", headers=headers
    ).json()
    assert page_one["total"] == 4
    assert page_one["pages"] == 2
    assert page_one["page_size"] == 2
    assert len(page_one["items"]) == 2
    assert page_one["has_next"] is True
    assert page_one["has_prev"] is False

    page_two = env["client"].get(
        "/documents/get_all?page=2&page_size=2", headers=headers
    ).json()
    assert len(page_two["items"]) == 2
    assert page_two["has_next"] is False
    assert page_two["has_prev"] is True


def test_invalid_pagination_and_sort_are_rejected(document_context):
    env = document_context
    headers = env["headers"]("admin")
    assert env["client"].get(
        "/documents/get_all?page_size=101", headers=headers
    ).status_code == 422
    assert env["client"].get(
        "/documents/get_all?page=0", headers=headers
    ).status_code == 422
    assert env["client"].get(
        "/documents/get_all?sort_by=content", headers=headers
    ).status_code == 400


def test_employee_sees_public_and_own_department_private_documents(document_context):
    env = document_context
    response = env["client"].get(
        "/documents/get_all", headers=env["headers"]("employee_a")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    titles = {item["title"] for item in body["items"]}
    assert "alpha_report" in titles
    assert "beta_notes" in titles
    assert "secret_report" in titles
    assert "confidential_report" not in titles


def test_employee_cannot_view_private_document_from_another_department(document_context):
    env = document_context
    document_id = _document_ids(env)["confidential_report"]
    response = env["client"].get(
        f"/documents/{document_id}", headers=env["headers"]("employee_a")
    )
    assert response.status_code == 403
    event = _audit_event(
        env, AuditAction.DOCUMENT_VIEW,
        user_id=env["ids"]["employee_a"], resource_id=document_id,
    )
    assert event is not None
    assert event.result == AuditResult.FAILURE.value


def test_search_respects_visibility_and_department_scope_and_is_audited(document_context):
    env = document_context
    employee = env["headers"]("employee_a")
    admin = env["headers"]("admin")

    response = env["client"].get(
        "/documents/search_document?q=secret", headers=employee
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert env["client"].get(
        "/documents/search_document?q=confidential", headers=employee
    ).json()["total"] == 0
    assert env["client"].get(
        "/documents/search_document?q=beta", headers=employee
    ).json()["total"] == 1
    assert env["client"].get(
        "/documents/search_document?q=secret", headers=admin
    ).json()["total"] == 1

    event = _audit_event(
        env, AuditAction.DOCUMENT_SEARCH, user_id=env["ids"]["employee_a"]
    )
    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_admin_can_upload_document_and_upload_is_audited(document_context):
    env = document_context
    response = env["client"].post(
        "/documents/add",
        data={
            "title": "new contract",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("contract.pdf", b"%PDF-1.4 new", "application/pdf")},
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "new_contract"
    assert body["department_name"] == "Front Office"
    stored_name = Path(body["file_path"]).name
    assert (env["documents_dir"] / stored_name).exists()

    event = _audit_event(
        env, AuditAction.DOCUMENT_CREATE, resource_id=body["id"]
    )
    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_duplicate_upload_returns_conflict_and_is_audited(document_context):
    env = document_context
    response = env["client"].post(
        "/documents/add",
        data={
            "title": "alpha report",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("alpha.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 409
    event = _audit_event(env, AuditAction.DOCUMENT_CREATE)
    assert event is not None
    assert event.result == AuditResult.FAILURE.value


def test_employee_cannot_create_update_or_delete_documents(document_context):
    env = document_context
    headers = env["headers"]("employee_a")
    create_response = env["client"].post(
        "/documents/add",
        data={
            "title": "blocked",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("blocked.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=headers,
    )
    assert create_response.status_code == 403

    document_id = _document_ids(env)["alpha_report"]
    update_response = env["client"].put(
        f"/documents/update/{document_id}",
        data={
            "title": "unauthorized update",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("updated.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=headers,
    )
    delete_response = env["client"].delete(
        f"/documents/delete/{document_id}", headers=headers
    )
    assert update_response.status_code == 403
    assert delete_response.status_code == 403

    create_event = _audit_event(
        env, AuditAction.DOCUMENT_CREATE, user_id=env["ids"]["employee_a"]
    )
    update_event = _audit_event(
        env, AuditAction.DOCUMENT_UPDATE, user_id=env["ids"]["employee_a"],
        resource_id=document_id,
    )
    delete_event = _audit_event(
        env, AuditAction.DOCUMENT_DELETE, user_id=env["ids"]["employee_a"],
        resource_id=document_id,
    )
    assert create_event is not None and create_event.result == AuditResult.FAILURE.value
    assert update_event is not None and update_event.result == AuditResult.FAILURE.value
    assert delete_event is not None and delete_event.result == AuditResult.FAILURE.value


def test_upload_rejects_unsupported_extension(document_context):
    env = document_context
    response = env["client"].post(
        "/documents/add",
        data={
            "title": "bad file",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("bad.txt", b"hello", "text/plain")},
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 400


def test_update_archives_old_file_and_preserves_document_id(document_context):
    env = document_context
    document_id = _document_ids(env)["alpha_report"]
    response = env["client"].put(
        f"/documents/update/{document_id}",
        data={
            "title": "renamed report",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("renamed.pdf", b"%PDF-1.4 updated", "application/pdf")},
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["title"] == "renamed_report"
    assert (env["archive_dir"] / "public__front_office__alpha_report.pdf").exists()
    assert not (env["documents_dir"] / "public__front_office__alpha_report.pdf").exists()
    assert (env["documents_dir"] / "public__front_office__renamed_report.pdf").exists()

    event = _audit_event(
        env, AuditAction.DOCUMENT_UPDATE, resource_id=document_id
    )
    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_update_missing_document_returns_404_and_is_audited(document_context):
    env = document_context
    response = env["client"].put(
        "/documents/update/9999",
        data={
            "title": "nothing",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("nothing.pdf", b"%PDF-1.4 test", "application/pdf")},
        headers=env["headers"]("admin"),
    )
    assert response.status_code == 404
    event = _audit_event(
        env, AuditAction.DOCUMENT_UPDATE, resource_id=9999
    )
    assert event is not None
    assert event.result == AuditResult.FAILURE.value


def test_delete_removes_file_and_index_entry_and_is_audited(document_context):
    env = document_context
    document_id = _document_ids(env)["beta_notes"]
    response = env["client"].delete(
        f"/documents/delete/{document_id}", headers=env["headers"]("admin")
    )
    assert response.status_code == 204
    assert not (env["documents_dir"] / "private__front_office__beta_notes.pdf").exists()
    assert env["client"].get(
        "/documents/get_all", headers=env["headers"]("admin")
    ).json()["total"] == 3

    event = _audit_event(
        env, AuditAction.DOCUMENT_DELETE, resource_id=document_id
    )
    assert event is not None
    assert event.result == AuditResult.SUCCESS.value
    assert env["client"].delete(
        f"/documents/delete/{document_id}", headers=env["headers"]("admin")
    ).status_code == 404


def test_document_routes_require_authentication(document_context):
    env = document_context
    assert env["client"].get("/documents/get_all").status_code == 401
    assert env["client"].get("/documents/search_document?q=alpha").status_code == 401
    assert env["client"].post(
        "/documents/add",
        data={
            "title": "unauthenticated",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")},
    ).status_code == 401
