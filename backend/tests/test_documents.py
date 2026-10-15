from datetime import UTC, datetime

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
from app.models.document import Document
from app.models.user import User, UserRole, UserStatus


@pytest.fixture
def document_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as db:
        department_a = Department(name="Department A")
        department_b = Department(name="Department B")
        db.add_all([department_a, department_b])
        db.flush()

        admin = User(
            email="admin@example.com",
            password_hash=hash_password("TestPassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            department_id=department_a.id,
        )
        employee_a = User(
            email="employee.a@example.com",
            password_hash=hash_password("TestPassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=department_a.id,
        )
        employee_b = User(
            email="employee.b@example.com",
            password_hash=hash_password("TestPassword123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=department_b.id,
        )
        db.add_all([admin, employee_a, employee_b])
        db.flush()

        doc_a = Document(
            title="Department A report",
            department_id=department_a.id,
            file_path="department-a/report.pdf",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        doc_b = Document(
            title="Department B report",
            department_id=department_b.id,
            file_path="department-b/report.pdf",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add_all([doc_a, doc_b])
        db.commit()
        ids = {
            "admin": admin.id,
            "employee_a": employee_a.id,
            "employee_b": employee_b.id,
            "doc_a": doc_a.id,
            "doc_b": doc_b.id,
            "department_a": department_a.id,
        }

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.state.testing_session_factory = TestingSessionLocal

    with TestClient(app) as client:

        def headers(role_key):
            with TestingSessionLocal() as db:
                user = db.get(User, ids[role_key])
                token = create_access_token(
                    subject=str(user.id),
                    role=user.role.value,
                )
            return {"Authorization": f"Bearer {token}"}

        yield client, headers, ids

    app.dependency_overrides.clear()
    del app.state.testing_session_factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_employee_only_lists_own_department_documents(document_context):
    client, headers, ids = document_context

    response = client.get(
        "/documents",
        headers=headers("employee_a"),
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [ids["doc_a"]]


def test_employee_cannot_view_other_department_document_and_is_audited(
    document_context,
):
    client, headers, ids = document_context

    response = client.get(
        f"/documents/{ids['doc_b']}",
        headers=headers("employee_a"),
    )

    assert response.status_code == 403

    with client.app.state.testing_session_factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_VIEW.value,
                AuditLog.resource_id == ids["doc_b"],
            )
        )

    assert event is not None
    assert event.user_id == ids["employee_a"]
    assert event.result == AuditResult.FAILURE.value


def test_employee_cannot_create_documents(document_context):
    client, headers, ids = document_context

    response = client.post(
        "/documents",
        headers=headers("employee_a"),
        json={
            "title": "Unauthorized document",
            "department_id": ids["department_a"],
            "file_path": "reports/unauthorized.pdf",
        },
    )

    assert response.status_code == 403

    with client.app.state.testing_session_factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_CREATE.value,
                AuditLog.user_id == ids["employee_a"],
            )
        )

    assert event is not None
    assert event.result == AuditResult.FAILURE.value


def test_admin_can_list_all_departments(document_context):
    client, headers, ids = document_context

    response = client.get("/documents", headers=headers("admin"))

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {
        ids["doc_a"],
        ids["doc_b"],
    }


def test_admin_can_create_document_and_creation_is_audited(document_context):
    client, headers, ids = document_context

    response = client.post(
        "/documents",
        headers=headers("admin"),
        json={
            "title": "New report",
            "department_id": ids["department_a"],
            "file_path": "reports/new-report.pdf",
        },
    )

    assert response.status_code == 201
    assert response.json()["title"] == "New report"

    with client.app.state.testing_session_factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_CREATE.value,
                AuditLog.resource_id == response.json()["id"],
            )
        )

    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_document_path_traversal_is_rejected(document_context):
    client, headers, ids = document_context

    response = client.post(
        "/documents",
        headers=headers("admin"),
        json={
            "title": "Unsafe path",
            "department_id": ids["department_a"],
            "file_path": "../secrets/passwords.txt",
        },
    )

    assert response.status_code == 422


def test_admin_can_update_document_and_update_is_audited(document_context):
    client, headers, ids = document_context

    response = client.put(
        f"/documents/{ids['doc_a']}",
        headers=headers("admin"),
        json={"title": "Updated report"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Updated report"

    with client.app.state.testing_session_factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_UPDATE.value,
                AuditLog.resource_id == ids["doc_a"],
            )
        )

    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_admin_can_delete_document_and_deletion_is_audited(document_context):
    client, headers, ids = document_context

    response = client.delete(
        f"/documents/{ids['doc_a']}",
        headers=headers("admin"),
    )

    assert response.status_code == 204

    with client.app.state.testing_session_factory() as db:
        assert db.get(Document, ids["doc_a"]) is None
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_DELETE.value,
                AuditLog.resource_id == ids["doc_a"],
            )
        )

    assert event is not None
    assert event.result == AuditResult.SUCCESS.value


def test_employee_cannot_update_or_delete_documents(document_context):
    client, headers, ids = document_context
    employee_headers = headers("employee_a")

    update_response = client.put(
        f"/documents/{ids['doc_a']}",
        headers=employee_headers,
        json={"title": "Unauthorized update"},
    )
    delete_response = client.delete(
        f"/documents/{ids['doc_a']}",
        headers=employee_headers,
    )

    assert update_response.status_code == 403
    assert delete_response.status_code == 403


def test_document_routes_require_authentication(document_context):
    client, _, ids = document_context

    list_response = client.get("/documents")
    detail_response = client.get(f"/documents/{ids['doc_a']}")

    assert list_response.status_code == 401
    assert detail_response.status_code == 401


def test_document_search_is_audited_without_storing_query(document_context):
    client, headers, ids = document_context

    response = client.get(
        "/documents",
        params={"search": "Department A"},
        headers=headers("employee_a"),
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [ids["doc_a"]]

    with client.app.state.testing_session_factory() as db:
        event = db.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.DOCUMENT_SEARCH.value,
                AuditLog.user_id == ids["employee_a"],
            )
        )

    assert event is not None
    assert event.result == AuditResult.SUCCESS.value
