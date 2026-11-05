# SEC-01 T6 : chaque action d'audit est enregistrée, consultable via GET /audit et filtrable.

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select
from test_documents import document_context  # noqa: F401  (reused fixture)

from app.core.config import settings
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User
from app.services.audit import log_gateway_call
from app.services.llm_gateway import GatewayError, Outcome, ask
from app.services.rag_context import RetrievedChunk

PASSWORD = "Password123"
ANY_EMAIL = "admin@example.com"


@pytest.fixture
def env(document_context):  # noqa: F811
    return document_context


def _admin(env):
    return env["headers"]("admin")


def _document_ids(env):
    body = env["client"].get("/documents/get_all", headers=_admin(env)).json()
    return {item["title"]: item["id"] for item in body["items"]}


def _expired_token(env):
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(env["ids"]["admin"]),
            "role": "ADMIN",
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "jti": "traceability-expired",
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _upload(env, title, status):
    response = env["client"].post(
        "/documents/add",
        data={"title": title, "department_id": env["ids"]["front_office"], "visibility": "public"},
        files={"file": ("doc.pdf", b"%PDF-1.4 doc", "application/pdf")},
        headers=_admin(env),
    )
    assert response.status_code == status
    return response


def _update(env, document_id, status):
    response = env["client"].put(
        f"/documents/update/{document_id}",
        data={
            "title": "updated",
            "department_id": env["ids"]["front_office"],
            "visibility": "public",
        },
        files={"file": ("doc.pdf", b"%PDF-1.4 v2", "application/pdf")},
        headers=_admin(env),
    )
    assert response.status_code == status
    return document_id


def _expect(response, status):
    assert response.status_code == status
    return response


@dataclass(frozen=True)
class Scenario:
    action: AuditAction
    result: AuditResult
    actor: str | None  # key of env["ids"], None for anonymous events
    resource_type: ResourceType
    run: Callable  # performs the real HTTP call(s), returns the expected resource_id


def _login_success(env):
    _expect(
        env["client"].post(
            "/auth/login", json={"email": "employee@example.com", "password": PASSWORD}
        ),
        200,
    )
    return env["ids"]["employee_a"]


def _login_failure(env):
    _expect(
        env["client"].post(
            "/auth/login", json={"email": "employee@example.com", "password": "wrong"}
        ),
        401,
    )
    return env["ids"]["employee_a"]


def _logout(env):
    _expect(env["client"].post("/auth/logout", headers=env["headers"]("employee_a")), 200)
    return env["ids"]["employee_a"]


def _view_success(env):
    document_id = _document_ids(env)["Alpha Report"]
    _expect(
        env["client"].get(f"/documents/{document_id}", headers=env["headers"]("employee_a")), 200
    )
    return document_id


def _view_failure(env):
    document_id = _document_ids(env)["Secret Report"]
    _expect(
        env["client"].get(f"/documents/{document_id}", headers=env["headers"]("employee_a")), 403
    )
    return document_id


def _search(env):
    _expect(
        env["client"].get(
            "/documents/search_document?q=alpha", headers=env["headers"]("employee_a")
        ),
        200,
    )


def _create_success(env):
    return _upload(env, "traceability contract", 201).json()["id"]


def _create_failure(env):
    _upload(env, "alpha report", 409)


def _update_success(env):
    return _update(env, _document_ids(env)["Alpha Report"], 200)


def _update_failure(env):
    return _update(env, 9999, 404)


def _delete_success(env):
    document_id = _document_ids(env)["Beta Notes"]
    _expect(env["client"].delete(f"/documents/delete/{document_id}", headers=_admin(env)), 204)
    return document_id


def _delete_failure(env):
    _expect(env["client"].delete("/documents/delete/9999", headers=_admin(env)), 404)
    return 9999


def _invite(env, email, status):
    return _expect(
        env["client"].post(
            "/users/invitations",
            json={"email": email, "role": "EMPLOYEE", "department_id": env["ids"]["front_office"]},
            headers=_admin(env),
        ),
        status,
    )


def _user_create_success(env):
    return _invite(env, "invited@example.com", 201).json()["user_id"]


def _user_create_failure(env):
    _invite(env, "employee@example.com", 409)


def _user_update_success(env):
    user_id = env["ids"]["employee_b"]
    _expect(
        env["client"].patch(
            f"/users/{user_id}", json={"email": "renamed.b@example.com"}, headers=_admin(env)
        ),
        200,
    )
    return user_id


def _user_update_failure(env):
    _expect(
        env["client"].patch("/users/9999", json={"email": "x@example.com"}, headers=_admin(env)),
        404,
    )
    return 9999


def _status_success(env):
    user_id = env["ids"]["employee_b"]
    _expect(
        env["client"].post(f"/users/{user_id}/status", json={"active": False}, headers=_admin(env)),
        200,
    )
    return user_id


def _status_failure(env):
    _expect(
        env["client"].post("/users/9999/status", json={"active": False}, headers=_admin(env)), 404
    )
    return 9999


def _export(env):
    _expect(env["client"].get("/audit/export", headers=_admin(env)), 200)


def _authentication_failed(env):
    _expect(env["client"].get("/audit", headers={"Authorization": "Bearer not-a-jwt"}), 401)


def _access_denied(env):
    _expect(env["client"].get("/audit", headers=env["headers"]("employee_a")), 403)


def _session_expired(env):
    headers = {"Authorization": f"Bearer {_expired_token(env)}"}
    _expect(env["client"].get("/audit", headers=headers), 401)


def _token_missing(env):
    _expect(env["client"].get("/audit"), 401)


class _Provider:
    def __init__(self, error=None):
        self.error = error

    def complete(self, prompt, *, timeout_seconds):
        if self.error is not None:
            raise self.error
        return "Réponse."


def _llm_call(env, provider, outcome):
    """A real gateway call (no HTTP route exists yet, I-4), logged by log_gateway_call."""
    actor_id = env["ids"]["employee_a"]
    try:
        report = ask(
            "Quelle est la procédure ?",
            allowed_layers=["layer_a"],
            excluded_sources=["Prive__Direction_Generale_Strategie.pdf"],
            max_extracts=3,
            max_context_chars=1000,
            max_prompt_chars=1000,
            timeout_seconds=5,
            retriever=lambda question: [
                RetrievedChunk("Procédure.", {"source": "a.pdf", "business_layer": "layer_a"})
            ],
            assembler=lambda question, extracts: question,
            provider=provider,
            caller_id=actor_id,
        ).report
    except GatewayError as error:
        report = error.report
    assert report.outcome is outcome
    with env["session_factory"]() as db:
        log_gateway_call(db, user=db.get(User, actor_id), report=report, ip_address="203.0.113.5")
        db.commit()


def _llm_call_success(env):
    _llm_call(env, _Provider(), Outcome.ANSWERED)


def _llm_call_failure(env):
    _llm_call(env, _Provider(error=RuntimeError("provider down")), Outcome.FAILED)


A, R, T = AuditAction, AuditResult, ResourceType
SCENARIOS = [
    Scenario(A.LOGIN, R.SUCCESS, "employee_a", T.USER, _login_success),
    Scenario(A.LOGIN, R.FAILURE, "employee_a", T.USER, _login_failure),
    Scenario(A.LOGOUT, R.SUCCESS, "employee_a", T.USER, _logout),
    Scenario(A.DOCUMENT_VIEW, R.SUCCESS, "employee_a", T.DOCUMENT, _view_success),
    Scenario(A.DOCUMENT_VIEW, R.FAILURE, "employee_a", T.DOCUMENT, _view_failure),
    Scenario(A.DOCUMENT_SEARCH, R.SUCCESS, "employee_a", T.DOCUMENT, _search),
    Scenario(A.DOCUMENT_CREATE, R.SUCCESS, "admin", T.DOCUMENT, _create_success),
    Scenario(A.DOCUMENT_CREATE, R.FAILURE, "admin", T.DOCUMENT, _create_failure),
    Scenario(A.DOCUMENT_UPDATE, R.SUCCESS, "admin", T.DOCUMENT, _update_success),
    Scenario(A.DOCUMENT_UPDATE, R.FAILURE, "admin", T.DOCUMENT, _update_failure),
    Scenario(A.DOCUMENT_DELETE, R.SUCCESS, "admin", T.DOCUMENT, _delete_success),
    Scenario(A.DOCUMENT_DELETE, R.FAILURE, "admin", T.DOCUMENT, _delete_failure),
    Scenario(A.USER_CREATE, R.SUCCESS, "admin", T.USER, _user_create_success),
    Scenario(A.USER_CREATE, R.FAILURE, "admin", T.USER, _user_create_failure),
    Scenario(A.USER_UPDATE, R.SUCCESS, "admin", T.USER, _user_update_success),
    Scenario(A.USER_UPDATE, R.FAILURE, "admin", T.USER, _user_update_failure),
    Scenario(A.USER_STATUS_UPDATE, R.SUCCESS, "admin", T.USER, _status_success),
    Scenario(A.USER_STATUS_UPDATE, R.FAILURE, "admin", T.USER, _status_failure),
    Scenario(A.EXPORT, R.SUCCESS, "admin", T.SESSION, _export),
    Scenario(A.AUTHENTICATION_FAILED, R.FAILURE, None, T.SESSION, _authentication_failed),
    Scenario(A.ACCESS_DENIED, R.FAILURE, "employee_a", T.AUDIT_LOG, _access_denied),
    Scenario(A.SESSION_EXPIRED, R.FAILURE, None, T.SESSION, _session_expired),
    Scenario(A.TOKEN_MISSING, R.FAILURE, None, T.SESSION, _token_missing),
    Scenario(A.LLM_CALL, R.SUCCESS, "employee_a", T.LLM_GATEWAY, _llm_call_success),
    Scenario(A.LLM_CALL, R.FAILURE, "employee_a", T.LLM_GATEWAY, _llm_call_failure),
]
SCENARIO_IDS = [f"{s.action.value}-{s.result.value}" for s in SCENARIOS]


def test_every_audit_action_has_a_scenario():
    assert len(SCENARIOS) == 25
    assert {s.action for s in SCENARIOS} == set(AuditAction)
    assert len(set(SCENARIO_IDS)) == len(SCENARIOS)


def _max_event_id(env):
    with env["session_factory"]() as db:
        return db.scalar(select(func.max(AuditLog.id))) or 0


def _events_after(env, event_id):
    with env["session_factory"]() as db:
        return db.scalars(select(AuditLog).where(AuditLog.id > event_id)).all()


def _query_ids(env, query):
    response = env["client"].get(f"/audit?page_size=100&{query}", headers=_admin(env))
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


def _department_name(env, department_id):
    if department_id is None:
        return None
    with env["session_factory"]() as db:
        return db.get(Department, department_id).name


def _email_of(env, user_id):
    if user_id is None:
        return None
    with env["session_factory"]() as db:
        return db.get(User, user_id).email


@pytest.mark.parametrize("scenario", SCENARIOS, ids=SCENARIO_IDS)
def test_action_is_recorded_consultable_and_filterable(env, scenario):
    before = _max_event_id(env)
    expected_resource_id = scenario.run(env)

    # 1. Recorded: exactly one new event, with the expected values.
    [event] = _events_after(env, before)
    actor_id = env["ids"][scenario.actor] if scenario.actor else None
    assert event.action == scenario.action.value
    assert event.result == scenario.result.value
    assert event.user_id == actor_id
    assert event.resource_type == scenario.resource_type.value
    assert event.resource_id == expected_resource_id

    # 2. Consultable: GET /audit returns the event with the same field values.
    response = env["client"].get(
        f"/audit?action={scenario.action.value}&page_size=100", headers=_admin(env)
    )
    assert response.status_code == 200
    [item] = [i for i in response.json()["items"] if i["id"] == event.id]
    assert item == {
        "id": event.id,
        "user_id": event.user_id,
        "user_email": _email_of(env, event.user_id),
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "result": event.result,
        "ip_address": event.ip_address,
        "timestamp": event.timestamp.isoformat(),
        "user_department_id": event.user_department_id,
        "user_department_name": _department_name(env, event.user_department_id),
        "resource_department_id": event.resource_department_id,
        "resource_department_name": _department_name(env, event.resource_department_id),
        "duration_ms": event.duration_ms,
        "details": event.details,
    }

    # 3. Filterable, on this exact event.
    action = f"action={scenario.action.value}"
    opposite = "FAILURE" if scenario.result is AuditResult.SUCCESS else "SUCCESS"
    assert event.id in _query_ids(env, f"{action}&result={scenario.result.value}")
    assert event.id not in _query_ids(env, f"{action}&result={opposite}")

    if event.user_id is None:
        # Anonymous events never match a user_email filter.
        assert event.id not in _query_ids(env, f"{action}&user_email={ANY_EMAIL}")
    else:
        email = item["user_email"]
        other = "employee@example.com" if email == ANY_EMAIL else ANY_EMAIL
        partial = email.split("@")[0].upper()
        assert event.id in _query_ids(env, f"{action}&user_email={email}")
        assert event.id in _query_ids(env, f"{action}&user_email={partial}")
        assert event.id not in _query_ids(env, f"{action}&user_email={other}")

    second = timedelta(seconds=1)
    window = f"date_from={(event.timestamp - second).isoformat()}"
    window += f"&date_to={(event.timestamp + second).isoformat()}"
    earlier = f"date_from={(event.timestamp - 10 * second).isoformat()}"
    earlier += f"&date_to={(event.timestamp - 5 * second).isoformat()}"
    assert event.id in _query_ids(env, f"{action}&{window}")
    assert event.id not in _query_ids(env, f"{action}&{earlier}")
    assert event.id in _query_ids(env, f"{action}&period=today")


# --- Filters ---------------------------------------------------------------------------


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _insert(env, timestamp, *, user="admin", action=AuditAction.LOGIN, result=AuditResult.SUCCESS):
    # Backdated rows: only used to prepare period and date-range tests.
    with env["session_factory"]() as db:
        event = AuditLog(
            user_id=env["ids"][user] if user else None,
            action=action.value,
            resource_type=ResourceType.USER.value,
            resource_id=None,
            result=result.value,
            timestamp=timestamp,
            ip_address="203.0.113.9",
        )
        db.add(event)
        db.commit()
        return event.id


def test_period_filters_select_the_expected_rows(env):
    now = _now()
    stamps = {
        "today": now,
        "3_days": now - timedelta(days=3),
        "10_days": now - timedelta(days=10),
        "40_days": now - timedelta(days=40),
    }
    ids = {key: _insert(env, stamp) for key, stamp in stamps.items()}
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_month = start_of_day.replace(day=1)

    def expected(start):
        return {ids[key] for key, stamp in stamps.items() if stamp >= start}

    assert _query_ids(env, "period=today") == expected(start_of_day)
    assert _query_ids(env, "period=last7") == {ids["today"], ids["3_days"]}
    assert _query_ids(env, "period=last30") == {ids["today"], ids["3_days"], ids["10_days"]}
    assert _query_ids(env, "period=month") == expected(start_of_month)
    assert _query_ids(env, "period=all") == set(ids.values())


def test_date_bounds_are_inclusive(env):
    base = _now().replace(microsecond=0) - timedelta(hours=1)
    first, middle, last = (_insert(env, base + timedelta(minutes=m)) for m in (0, 10, 20))
    at = (base + timedelta(minutes=10)).isoformat()

    assert _query_ids(env, f"date_from={at}&date_to={at}") == {middle}
    assert _query_ids(env, f"date_from={at}") == {middle, last}
    assert _query_ids(env, f"date_to={at}") == {first, middle}


def test_empty_date_window_returns_nothing(env):
    _insert(env, _now())
    future = (_now() + timedelta(days=1)).isoformat()
    later = (_now() + timedelta(days=2)).isoformat()
    body = (
        env["client"].get(f"/audit?date_from={future}&date_to={later}", headers=_admin(env)).json()
    )
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.parametrize("field", ["date_from", "date_to"])
def test_malformed_date_is_rejected(env, field):
    response = env["client"].get(f"/audit?{field}=not-a-date", headers=_admin(env))
    assert response.status_code == 422


def test_user_email_excludes_anonymous_events_and_matches_partially(env):
    now = _now()
    admin_event = _insert(env, now, user="admin")
    employee_event = _insert(env, now, user="employee_a")
    anonymous_event = _insert(env, now, user=None, result=AuditResult.FAILURE)

    assert _query_ids(env, "") == {admin_event, employee_event, anonymous_event}
    assert _query_ids(env, "user_email=EXAMPLE.COM") == {admin_event, employee_event}
    assert _query_ids(env, "user_email=employee@") == {employee_event}
    assert _query_ids(env, "user_email=nobody@") == set()


def test_combined_filters_return_the_intersection(env):
    now = _now()
    old = now - timedelta(days=2)
    target = {
        _insert(env, now, user="employee_a", action=AuditAction.LOGIN, result=AuditResult.FAILURE),
        _insert(env, now, user="employee_a", action=AuditAction.LOGIN, result=AuditResult.FAILURE),
    }
    # Each row below differs from the target on exactly one filter.
    _insert(env, now, user="employee_a", action=AuditAction.LOGIN, result=AuditResult.SUCCESS)
    _insert(env, now, user="employee_a", action=AuditAction.LOGOUT, result=AuditResult.FAILURE)
    _insert(env, now, user="admin", action=AuditAction.LOGIN, result=AuditResult.FAILURE)
    _insert(env, old, user="employee_a", action=AuditAction.LOGIN, result=AuditResult.FAILURE)

    since = (now - timedelta(hours=1)).isoformat()
    query = f"action=LOGIN&result=FAILURE&user_email=employee@&date_from={since}"
    body = env["client"].get(f"/audit?{query}", headers=_admin(env)).json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == target


def test_pagination_with_filters_is_consistent(env):
    now = _now()
    expected = [
        _insert(env, now - timedelta(minutes=m), action=AuditAction.EXPORT) for m in range(5)
    ]
    _insert(env, now, action=AuditAction.LOGIN)

    seen = []
    for page in (1, 2, 3):
        body = (
            env["client"]
            .get(f"/audit?action=EXPORT&page={page}&page_size=2", headers=_admin(env))
            .json()
        )
        assert body["total"] == 5
        assert body["pages"] == 3
        assert body["has_prev"] is (page > 1)
        assert body["has_next"] is (page < 3)
        seen.extend(item["id"] for item in body["items"])

    # Newest first, every matching row exactly once across pages.
    assert seen == expected
