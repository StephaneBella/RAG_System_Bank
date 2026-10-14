# Tests de gestion des utilisateurs

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
def env():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        front = Department(name='Front Office')
        compliance = Department(name='Compliance')
        db.add_all([front, compliance])
        db.flush()
        db.add_all([
            User(email='admin@example.com', password_hash=hash_password('Password123'), status=UserStatus.ACTIVE, role=UserRole.ADMIN, department_id=front.id),
            User(email='employee@example.com', password_hash=hash_password('Password123'), status=UserStatus.ACTIVE, role=UserRole.EMPLOYEE, department_id=front.id),
            User(email='second@example.com', password_hash=hash_password('Password123'), status=UserStatus.ACTIVE, role=UserRole.EMPLOYEE, department_id=compliance.id),
        ])
        db.commit()
        front_id, compliance_id = front.id, compliance.id
    def override_get_db():
        with SessionLocal() as db:
            yield db
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield {'client': client, 'front_id': front_id, 'compliance_id': compliance_id}
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()

def headers(client, email='admin@example.com'):
    response = client.post('/auth/login', json={'email': email, 'password': 'Password123'})
    assert response.status_code == 200
    return {'Authorization': f'Bearer {response.json()["access_token"]}'}


def test_users_list_pagination_and_metadata(env):
    client = env['client']
    response = client.get('/users?page=1&page_size=2', headers=headers(client))
    assert response.status_code == 200
    body = response.json()
    assert body['total'] == 3
    assert body['pages'] == 2
    assert len(body['items']) == 2
    assert body['has_next'] is True
    assert body['has_prev'] is False
    second = client.get('/users?page=2&page_size=2', headers=headers(client)).json()
    assert len(second['items']) == 1
    assert second['has_next'] is False
    assert second['has_prev'] is True

def test_users_reject_invalid_pagination_and_sort(env):
    client = env['client']
    auth = headers(client)
    assert client.get('/users?page=0', headers=auth).status_code == 422
    assert client.get('/users?page_size=101', headers=auth).status_code == 422
    assert client.get('/users?sort_by=password_hash', headers=auth).status_code == 422
    assert client.get('/users?order=sideways', headers=auth).status_code == 422

def test_users_filter_by_role_and_department(env):
    client = env['client']
    auth = headers(client)
    by_role = client.get('/users?role=ADMIN', headers=auth)
    assert by_role.status_code == 200
    assert [item['email'] for item in by_role.json()['items']] == ['admin@example.com']
    by_department = client.get(f"/users?department_id={env['compliance_id']}", headers=auth)
    assert by_department.status_code == 200
    assert [item['email'] for item in by_department.json()['items']] == ['second@example.com']

def test_users_searches_email_and_department(env):
    client = env['client']
    auth = headers(client)
    email_result = client.get('/users?search=second%40example.com', headers=auth).json()
    assert email_result['total'] == 1
    assert email_result['items'][0]['email'] == 'second@example.com'
    department_result = client.get('/users?search=Compliance', headers=auth).json()
    assert department_result['total'] == 1
    assert department_result['items'][0]['department_name'] == 'Compliance'

def test_users_sort_descending_by_email(env):
    client = env['client']
    response = client.get('/users?sort_by=email&order=desc', headers=headers(client))
    assert response.status_code == 200
    emails = [item['email'] for item in response.json()['items']]
    assert emails == sorted(emails, reverse=True)


def test_update_user_normalizes_email_and_changes_department_and_role(env):
    client = env['client']
    auth = headers(client)
    users = client.get('/users', headers=auth).json()['items']
    user_id = next(item['id'] for item in users if item['email'] == 'employee@example.com')
    response = client.patch(f'/users/{user_id}', headers=auth, json={'email': 'UPDATED@example.com', 'role': 'ADMIN', 'department_id': env['compliance_id']})
    assert response.status_code == 200
    body = response.json()
    assert body['email'] == 'updated@example.com'
    assert body['role'] == 'ADMIN'
    assert body['department_id'] == env['compliance_id']
    assert body['department_name'] == 'Compliance'

def test_update_rejects_duplicate_email(env):
    client = env['client']
    auth = headers(client)
    users = client.get('/users', headers=auth).json()['items']
    target = next(item for item in users if item['email'] == 'employee@example.com')
    response = client.patch(f"/users/{target['id']}", headers=auth, json={'email': 'admin@example.com'})
    assert response.status_code == 409

@pytest.mark.parametrize('payload', [{}, {'email': None}, {'department_id': 9999}])
def test_update_rejects_empty_null_or_unknown_department(env, payload):
    client = env['client']
    auth = headers(client)
    users = client.get('/users', headers=auth).json()['items']
    target = next(item for item in users if item['email'] == 'employee@example.com')
    response = client.patch(f"/users/{target['id']}", headers=auth, json=payload)
    assert response.status_code == 422

def test_update_missing_user_returns_404(env):
    client = env['client']
    response = client.patch('/users/9999', headers=headers(client), json={'email': 'nobody@example.com'})
    assert response.status_code == 404


@pytest.mark.parametrize('active,expected', [(False, 'INACTIVE'), (True, 'ACTIVE')])
def test_user_status_can_be_changed(env, active, expected):
    client = env['client']
    auth = headers(client)
    users = client.get('/users', headers=auth).json()['items']
    target = next(item for item in users if item['email'] == 'employee@example.com')
    response = client.post(f"/users/{target['id']}/status", headers=auth, json={'active': active})
    assert response.status_code == 200
    assert response.json()['status'] == expected

def test_status_missing_user_returns_404(env):
    client = env['client']
    response = client.post('/users/9999/status', headers=headers(client), json={'active': False})
    assert response.status_code == 404

def test_users_require_authentication(env):
    client = env['client']
    assert client.get('/users').status_code == 401
    assert client.patch('/users/1', json={'email': 'x@example.com'}).status_code == 401

def test_employee_user_list_permission_is_explicitly_checked(env):
    client = env['client']
    response = client.get('/users', headers=headers(client, 'employee@example.com'))
    assert response.status_code == 403

def test_department_list_requires_authentication(env):
    client = env['client']
    assert client.get('/departments').status_code == 401
    response = client.get('/departments', headers=headers(client))
    assert response.status_code == 200
    assert {item['name'] for item in response.json()} == {'Front Office', 'Compliance'}


def test_employee_cannot_update_user(env):
    client = env["client"]
    employee_auth = headers(client, "employee@example.com")
    response = client.patch(
        "/users/1",
        headers=employee_auth,
        json={"email": "unauthorized@example.com"},
    )
    assert response.status_code == 403


def test_employee_cannot_change_user_status(env):
    client = env["client"]
    employee_auth = headers(client, "employee@example.com")
    response = client.post(
        "/users/1/status",
        headers=employee_auth,
        json={"active": False},
    )
    assert response.status_code == 403


def test_employee_can_list_departments(env):
    client = env["client"]
    employee_auth = headers(client, "employee@example.com")
    response = client.get("/departments", headers=employee_auth)
    assert response.status_code == 200
    assert {item["name"] for item in response.json()} == {"Front Office", "Compliance"}


def test_cannot_deactivate_last_active_admin(env):
    client = env["client"]
    auth = headers(client)
    users = client.get("/users", headers=auth).json()["items"]
    admin = next(item for item in users if item["email"] == "admin@example.com")

    response = client.post(
        f"/users/{admin['id']}/status",
        headers=auth,
        json={"active": False},
    )

    assert response.status_code == 422
    assert "last active administrator" in response.json()["detail"]


def test_cannot_demote_last_active_admin(env):
    client = env["client"]
    auth = headers(client)
    users = client.get("/users", headers=auth).json()["items"]
    admin = next(item for item in users if item["email"] == "admin@example.com")

    response = client.patch(
        f"/users/{admin['id']}",
        headers=auth,
        json={"role": "EMPLOYEE"},
    )

    assert response.status_code == 422
    assert "last active administrator" in response.json()["detail"]
