# US 07, étape 3 : journalisation des appels fournisseur de l'agent RAG (SEC-01).
#
# Les comptes rendus viennent d'un vrai LLMRequestContext, alimenté par un faux adaptateur
# sans fournisseur. Chaque appel devient un événement LLM_CALL portant details.provider_call ;
# les appels d'une même requête sont reliés par request_id et call_index. Valeurs
# synthétiques uniquement.

import csv
import io
import json
import re
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_suspicious_activity import IP, NOW, ctx  # noqa: F401  (reused fixture)

from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.user import User
from app.schemas.security_alert import AlertRule
from app.services import audit
from app.services.audit import (
    log_audit_event,
    log_gateway_call,
    log_provider_call,
    log_request_calls,
)
from app.services.llm_gateway import GatewayError, ask
from app.services.llm_request import (
    CallKind,
    CallLimitExceeded,
    CallOutcome,
    LLMRequestContext,
    PromptTooLong,
    RequestLimits,
)
from app.services.pseudonymization import EntityType
from app.services.rag_context import RetrievedChunk

NAME = "Awa Ndiaye"
EMAIL = "awa.test@example.org"
IBAN = "FR76 3000 6000 0112 3456 7890 189"
QUESTION = f"Le dossier de {NAME} ({EMAIL}, IBAN {IBAN}) est-il complet ?"
TOOL_ARGUMENT = f'{{"query": "procédure {NAME}"}}'
SESSION_ID = "session-Awa-Ndiaye-42"  # client supplied: must never be logged
TOKEN = re.compile(r"\[(PERSONNE|CLIENT|DOSSIER)_\d+_[0-9a-f]{32}\]")
PROVIDER_KEYS = {
    "request_id",
    "call_index",
    "call_kind",
    "outcome",
    "error_type",
    "prompt_chars",
    "answer_chars",
    "tokens_sent",
    "tokens_restored",
    "tokens_withheld",
    "duration_ms",
}


def request(caller_id, **limits):
    values = {"max_provider_calls": 10, "max_prompt_chars": 5000, "timeout_seconds": 30.0}
    return LLMRequestContext(
        caller_id=caller_id,
        limits=RequestLimits(**{**values, **limits}),
        known_values={EntityType.PERSON: [NAME]},
    )


def provider_call(context, kind=CallKind.AGENT_STEP, text=QUESTION, error=None):
    """One call of a fake adapter: protect, send, then restore the answer and a tool argument."""
    with context.call(kind) as call:
        prompt = context.protect(text)
        call.sent(prompt)
        if error is not None:
            raise error
        token = TOKEN.search(prompt).group()
        call.restore(f"Réponse sur {token}.")
        call.restore(context.protect(TOOL_ARGUMENT))


def outcome_reports(caller_id):
    """One report for each outcome."""
    context = request(caller_id, max_provider_calls=3, max_prompt_chars=200)
    provider_call(context)
    with pytest.raises(PromptTooLong):
        provider_call(context, text=QUESTION + "x" * 300)
    with pytest.raises(RuntimeError):
        provider_call(context, error=RuntimeError(NAME))
    with pytest.raises(CallLimitExceeded):
        provider_call(context)
    context.close()
    return {report.outcome: report for report in context.reports()}


def _user(db, env, key="employee"):
    return db.get(User, env["ids"][key])


def _log_one(env, report, user="employee", ip=IP):
    with env["session_factory"]() as db:
        log_provider_call(db, user=_user(db, env, user), report=report, ip_address=ip)
        db.commit()


def _log_request(env, context, user="employee", ip=IP):
    with env["session_factory"]() as db:
        written = log_request_calls(db, user=_user(db, env, user), context=context, ip_address=ip)
        db.commit()
    return written


def _events(env):
    with env["session_factory"]() as db:
        return list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.action == AuditAction.LLM_CALL.value)
                .order_by(AuditLog.id)
            )
        )


def _assert_clean(text):
    for value in (NAME, EMAIL, IBAN, QUESTION, "dossier", "Réponse", "procédure", SESSION_ID):
        assert value not in text, value
    assert TOKEN.search(text) is None


# --- Correspondance des résultats et ensemble exact des clés -----------------------------


@pytest.mark.parametrize(
    ("outcome", "result"),
    [
        (CallOutcome.COMPLETED, AuditResult.SUCCESS),
        (CallOutcome.PROMPT_TOO_LONG, AuditResult.SUCCESS),
        (CallOutcome.CALL_LIMIT_EXCEEDED, AuditResult.FAILURE),
        (CallOutcome.FAILED, AuditResult.FAILURE),
    ],
)
def test_each_outcome_is_logged_as_one_event(ctx, outcome, result):  # noqa: F811
    _log_one(ctx, outcome_reports(ctx["ids"]["employee"])[outcome])
    [event] = _events(ctx)
    assert (event.action, event.resource_type) == ("LLM_CALL", "LLM_GATEWAY")
    assert event.result == result.value
    assert event.user_id == ctx["ids"]["employee"]
    assert (event.resource_id, event.resource_department_id) == (None, None)
    assert event.ip_address == IP
    assert event.details["provider_call"]["outcome"] == outcome.value


def test_the_result_mapping_covers_every_outcome():
    assert set(audit.PROVIDER_CALL_RESULTS) == set(CallOutcome)


def test_details_hold_exactly_the_provider_call_keys(ctx):  # noqa: F811
    for report in outcome_reports(ctx["ids"]["employee"]).values():
        _log_one(ctx, report)
    for event in _events(ctx):
        assert set(event.details) == {"provider_call"}
        assert set(event.details["provider_call"]) == PROVIDER_KEYS


def test_logged_values_are_the_report_counters(ctx):  # noqa: F811
    reports = outcome_reports(ctx["ids"]["employee"])
    for report in reports.values():
        _log_one(ctx, report)
    logged = {
        e.details["provider_call"]["outcome"]: e.details["provider_call"] for e in _events(ctx)
    }
    completed = reports[CallOutcome.COMPLETED]
    assert logged["COMPLETED"] == {
        "request_id": completed.request_id,
        "call_index": 0,
        "call_kind": "AGENT_STEP",
        "outcome": "COMPLETED",
        "error_type": None,
        "prompt_chars": None,  # H8: never logged
        "answer_chars": completed.answer_chars,
        "tokens_sent": 1,
        "tokens_restored": 2,  # the answer and the tool argument
        "tokens_withheld": 0,
        "duration_ms": completed.duration_ms,
    }
    assert logged["PROMPT_TOO_LONG"]["answer_chars"] is None
    assert logged["FAILED"]["error_type"] == "RuntimeError"
    assert all(v["error_type"] is None for k, v in logged.items() if k != "FAILED")


# --- Appels multiples d'une même requête --------------------------------------------------


def test_the_calls_of_one_request_share_its_request_id(ctx):  # noqa: F811
    context = request(ctx["ids"]["employee"])
    kinds = [
        CallKind.AGENT_STEP,
        CallKind.QUERY_REWRITE,
        CallKind.SELF_QUERY,
        CallKind.QUERY_FUSION,
    ]
    for kind in kinds:
        provider_call(context, kind)
    context.close()
    assert _log_request(ctx, context) == 4
    logged = [event.details["provider_call"] for event in _events(ctx)]
    assert {item["request_id"] for item in logged} == {context.request_id}
    assert [item["call_index"] for item in logged] == [0, 1, 2, 3]
    assert [item["call_kind"] for item in logged] == [kind.value for kind in kinds]


def test_two_requests_have_distinct_request_ids(ctx):  # noqa: F811
    contexts = [request(ctx["ids"]["employee"]) for _ in range(2)]
    for context in contexts:
        provider_call(context)
        provider_call(context, CallKind.QUERY_REWRITE)
        context.close()
        _log_request(ctx, context)
    by_request = {}
    for event in _events(ctx):
        item = event.details["provider_call"]
        by_request.setdefault(item["request_id"], []).append(item["call_index"])
    assert by_request == {contexts[0].request_id: [0, 1], contexts[1].request_id: [0, 1]}


def test_a_request_without_call_writes_nothing(ctx):  # noqa: F811
    context = request(ctx["ids"]["employee"])
    context.close()
    assert _log_request(ctx, context) == 0
    assert _events(ctx) == []


def test_the_calls_of_one_request_are_kept_apart_from_the_user_requests(ctx):  # noqa: F811
    # Three questions, each with four provider calls: twelve events, three requests.
    for _ in range(3):
        context = request(ctx["ids"]["employee"])
        for kind in CallKind:
            provider_call(context, kind)
        context.close()
        _log_request(ctx, context)
    logged = [event.details["provider_call"] for event in _events(ctx)]
    assert len(logged) == 12
    assert len({item["request_id"] for item in logged}) == 3
    assert sum(item["call_index"] == 0 for item in logged) == 3


# --- Identités invalides et contournement -------------------------------------------------


@pytest.mark.parametrize("caller", ["other", "admin"])
def test_a_report_of_another_user_is_refused(ctx, caller):  # noqa: F811
    report = outcome_reports(ctx["ids"][caller])[CallOutcome.COMPLETED]
    with pytest.raises(ValueError, match="caller_id"):
        _log_one(ctx, report, user="employee")
    assert _events(ctx) == []


@pytest.mark.parametrize("caller_id", [True, False, "1", 1.0, None])
def test_a_non_int_caller_id_is_refused_even_when_equal(ctx, caller_id):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[CallOutcome.COMPLETED]
    with ctx["session_factory"]() as db:
        user = _user(db, ctx)
        if caller_id is True:
            user.id = 1  # True == 1: the bool must still be refused
        with pytest.raises(ValueError, match="caller_id"):
            log_provider_call(
                db, user=user, report=replace(report, caller_id=caller_id), ip_address=IP
            )
        db.rollback()
    assert _events(ctx) == []


def test_a_context_of_another_user_is_refused_without_writing(ctx):  # noqa: F811
    context = request(ctx["ids"]["other"])
    provider_call(context)
    context.close()
    with pytest.raises(ValueError, match="caller_id"):
        _log_request(ctx, context, user="employee")
    assert _events(ctx) == []


def test_only_a_user_a_report_a_context_and_a_str_ip_are_accepted(ctx):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[CallOutcome.COMPLETED]
    with ctx["session_factory"]() as db:
        user = _user(db, ctx)
        for kwargs in (
            {"user": None, "report": report, "ip_address": IP},
            {"user": user, "report": {"outcome": "COMPLETED"}, "ip_address": IP},
            {"user": user, "report": report, "ip_address": 12},
        ):
            with pytest.raises(TypeError):
                log_provider_call(db, **kwargs)
        with pytest.raises(TypeError):
            log_request_calls(db, user=user, context=[report], ip_address=IP)
        with pytest.raises(TypeError):
            log_request_calls(db, user=None, context=request(user.id), ip_address=IP)
    assert _events(ctx) == []


def test_log_audit_event_offers_no_provider_call_path(ctx):  # noqa: F811
    report = outcome_reports(ctx["ids"]["other"])[CallOutcome.COMPLETED]
    with ctx["session_factory"]() as db, pytest.raises(TypeError):
        log_audit_event(
            db,
            user=_user(db, ctx),
            action=AuditAction.LLM_CALL,
            resource_type=ResourceType.LLM_GATEWAY,
            resource_id=None,
            result=AuditResult.SUCCESS,
            provider_call=report,
        )
    assert _events(ctx) == []


def test_an_event_cannot_be_both_a_gateway_call_and_a_provider_call(ctx):  # noqa: F811
    with ctx["session_factory"]() as db, pytest.raises(ValueError, match="either"):
        audit._add_event(
            db,
            user=_user(db, ctx),
            action=AuditAction.LLM_CALL,
            resource_type=ResourceType.LLM_GATEWAY,
            resource_id=None,
            result=AuditResult.SUCCESS,
            ip_address=IP,
            resource_department_id=None,
            details={"llm_call": {}, "provider_call": {}},
        )
    assert _events(ctx) == []


# --- Rapports étrangers ou mal formés, et absence d'écriture partielle -------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "A" * 32},
        {"request_id": "a" * 31},
        {"request_id": "g" * 32},
        {"request_id": f"{NAME:<32}"},
        {"request_id": None},
        {"call_index": -1},
        {"call_index": True},
        {"call_index": "0"},
        {"kind": "AGENT_STEP"},
        {"outcome": "COMPLETED"},
        {"error_type": "RuntimeError"},
        {"prompt_chars": None},
        {"prompt_chars": -1},
        {"answer_chars": 1.5},
        {"tokens_sent": -1},
        {"tokens_restored": "2"},
        {"tokens_withheld": None},
        {"duration_ms": 1.5},
    ],
)
def test_a_malformed_report_is_refused_without_writing(ctx, changes):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[CallOutcome.COMPLETED]
    with pytest.raises((TypeError, ValueError)):
        _log_one(ctx, replace(report, **changes))
    assert _events(ctx) == []


@pytest.mark.parametrize(
    ("outcome", "changes"),
    [
        (CallOutcome.FAILED, {"error_type": None}),
        (CallOutcome.CALL_LIMIT_EXCEEDED, {"prompt_chars": 10}),
        (CallOutcome.CALL_LIMIT_EXCEEDED, {"answer_chars": 10}),
        (CallOutcome.CALL_LIMIT_EXCEEDED, {"tokens_sent": 1}),
        (CallOutcome.CALL_LIMIT_EXCEEDED, {"tokens_restored": 1}),
        (CallOutcome.CALL_LIMIT_EXCEEDED, {"error_type": "RuntimeError"}),
        (CallOutcome.PROMPT_TOO_LONG, {"answer_chars": 10}),
        (CallOutcome.PROMPT_TOO_LONG, {"tokens_withheld": 1}),
        (CallOutcome.PROMPT_TOO_LONG, {"tokens_sent": 1}),
        (CallOutcome.PROMPT_TOO_LONG, {"prompt_chars": None}),
    ],
)
def test_an_inconsistent_report_is_refused_without_writing(ctx, outcome, changes):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[outcome]
    with pytest.raises(ValueError):
        _log_one(ctx, replace(report, **changes))
    assert _events(ctx) == []


def _context_with_reports(env, monkeypatch, make_reports):
    context = request(env["ids"]["employee"])
    for kind in CallKind:
        provider_call(context, kind)
    context.close()  # closed: only the injected report can be the reason for a refusal
    reports = make_reports(context, context.reports())
    monkeypatch.setattr(context, "reports", lambda: reports)
    return context


@pytest.mark.parametrize(
    ("make_reports", "error", "reason"),
    [
        # the last report is malformed
        (lambda c, r: (*r[:-1], replace(r[-1], tokens_sent=-1)), ValueError, "tokens_sent"),
        # a report of another request
        (
            lambda c, r: (*r[:-1], replace(r[-1], request_id="0" * 32)),
            ValueError,
            "belong to this request",
        ),
        # a report of another user
        (
            lambda c, r: (*r[:-1], replace(r[-1], caller_id=c.caller_id + 1)),
            ValueError,
            "caller_id",
        ),
        # two reports with the same index
        (lambda c, r: (*r[:-1], replace(r[-1], call_index=0)), ValueError, "unique"),
        # a gateway-like object in the list
        (lambda c, r: (*r[:-1], {"request_id": c.request_id}), TypeError, "ProviderCallReport"),
    ],
)
def test_one_refused_report_means_no_event_for_the_request(
    ctx,  # noqa: F811
    monkeypatch,
    make_reports,
    error,
    reason,
):
    context = _context_with_reports(ctx, monkeypatch, make_reports)
    with pytest.raises(error, match=reason):
        _log_request(ctx, context)
    assert _events(ctx) == []


def test_a_genuine_report_of_another_request_is_refused(ctx, monkeypatch):  # noqa: F811
    foreign = request(ctx["ids"]["employee"])  # same user, another request
    provider_call(foreign)
    context = request(ctx["ids"]["employee"])
    provider_call(context)
    context.close()
    mixed = (*context.reports(), replace(foreign.reports()[0], call_index=1))
    monkeypatch.setattr(context, "reports", lambda: mixed)
    with pytest.raises(ValueError, match="belong to this request"):
        _log_request(ctx, context)
    assert _events(ctx) == []


# --- Contexte fermé avant journalisation ---------------------------------------------------


def test_an_open_context_is_refused_without_any_event(ctx):  # noqa: F811
    context = request(ctx["ids"]["employee"])
    for kind in CallKind:
        provider_call(context, kind)
    with pytest.raises(ValueError, match="closed"):
        _log_request(ctx, context)
    assert _events(ctx) == []
    assert not context.closed  # nothing was changed on the context


def test_an_entered_context_is_refused_until_it_is_exited(ctx):  # noqa: F811
    with request(ctx["ids"]["employee"]) as context:
        provider_call(context)
        with pytest.raises(ValueError, match="closed"):
            _log_request(ctx, context)
    assert _events(ctx) == []
    assert _log_request(ctx, context) == 1


def test_reports_stay_available_and_loggable_after_close(ctx):  # noqa: F811
    context = request(ctx["ids"]["employee"])
    for kind in CallKind:
        provider_call(context, kind)
    before = context.reports()
    context.close()
    assert context.reports() == before and len(before) == 4
    assert _log_request(ctx, context) == 4
    logged = [event.details["provider_call"] for event in _events(ctx)]
    assert [item["call_index"] for item in logged] == [0, 1, 2, 3]
    assert {item["request_id"] for item in logged} == {context.request_id}


# --- H8 : la longueur du prompt n'est jamais journalisée -----------------------------------


def _counters(logged):
    """Every integer logged for a call; the random request id is not a counter."""
    return [value for key, value in logged.items() if key != "request_id" and type(value) is int]


@pytest.mark.parametrize("outcome", list(CallOutcome))
def test_the_prompt_length_is_never_logged(ctx, outcome):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[outcome]
    if outcome is not CallOutcome.CALL_LIMIT_EXCEEDED:
        assert report.prompt_chars > 0  # the report knows it; the journal must not
    _log_one(ctx, report)
    [event] = _events(ctx)
    assert "prompt_chars" in event.details["provider_call"]  # the 11 keys are kept
    assert event.details["provider_call"]["prompt_chars"] is None
    if report.prompt_chars is not None:
        assert report.prompt_chars not in _counters(event.details["provider_call"])


def test_the_prompt_length_does_not_reach_the_api_nor_the_export(ctx):  # noqa: F811
    # A question whose length would be recognisable: 777 characters once protected.
    context = request(ctx["ids"]["employee"])
    provider_call(context, text="Q" * 777 + f" {NAME}")
    context.close()
    _log_request(ctx, context)
    client, admin = ctx["client"], ctx["headers"]("admin")
    listed = client.get("/audit", params={"action": "LLM_CALL"}, headers=admin)
    [item] = listed.json()["items"]
    exported = client.get("/audit/export", params={"action": "LLM_CALL"}, headers=admin)
    [row] = csv.DictReader(io.StringIO(exported.text))
    length = context.reports()[0].prompt_chars
    assert length > 777
    for logged in (item["details"]["provider_call"], json.loads(row["details"])["provider_call"]):
        assert logged["prompt_chars"] is None
        assert length not in _counters(logged)


def test_the_prompt_length_is_still_validated(ctx):  # noqa: F811
    completed = outcome_reports(ctx["ids"]["employee"])[CallOutcome.COMPLETED]
    for bad in (-1, "12", 1.5, True):
        with pytest.raises(ValueError, match="prompt_chars"):
            _log_one(ctx, replace(completed, prompt_chars=bad))
    assert _events(ctx) == []


# --- Cohérence des jetons et de la réponse --------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "changes", "reason"),
    [
        (CallOutcome.COMPLETED, {"answer_chars": None}, "answer it does not have"),
        (
            CallOutcome.COMPLETED,
            {"answer_chars": None, "tokens_restored": 0, "tokens_withheld": 1},
            "answer it does not have",
        ),
        (
            CallOutcome.FAILED,
            {"answer_chars": None, "tokens_withheld": 2},
            "answer it does not have",
        ),
        (CallOutcome.COMPLETED, {"tokens_sent": 0}, "never sent"),
        (
            CallOutcome.FAILED,
            {"answer_chars": 5, "tokens_sent": 0, "tokens_restored": 1},
            "never sent",
        ),
    ],
)
def test_token_counts_must_match_the_answer_and_what_was_sent(ctx, outcome, changes, reason):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[outcome]
    with pytest.raises(ValueError, match=reason):
        _log_one(ctx, replace(report, **changes))
    assert _events(ctx) == []


@pytest.mark.parametrize(
    "changes",
    [
        # a foreign token withheld while no token was sent
        {"tokens_sent": 0, "tokens_restored": 0, "tokens_withheld": 1},
        # an answer without any token
        {"tokens_restored": 0, "tokens_withheld": 0},
        # no answer and no token (a reply made of tool calls only)
        {"answer_chars": None, "tokens_restored": 0, "tokens_withheld": 0},
    ],
)
def test_consistent_token_counts_are_accepted(ctx, changes):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[CallOutcome.COMPLETED]
    _log_one(ctx, replace(report, **changes))
    assert len(_events(ctx)) == 1


def test_legitimate_reports_of_a_request_context_are_all_accepted(ctx):  # noqa: F811
    context = request(ctx["ids"]["employee"], max_prompt_chars=50)
    foreign = request(ctx["ids"]["employee"]).protect(NAME)  # token of another request
    with context.call(CallKind.AGENT_STEP) as call:  # nothing to send at all
        call.sent()
    with context.call(CallKind.AGENT_STEP) as call:  # tool calls only: no restore
        call.sent("Sans nom.")
    with context.call(CallKind.QUERY_REWRITE) as call:  # a foreign token is withheld
        call.sent("Sans nom.")
        call.restore(f"Voir {foreign}")
    with pytest.raises(PromptTooLong), context.call(CallKind.AGENT_STEP) as call:
        call.sent("x" * 60)  # over max_prompt_chars: refused before sending
    with pytest.raises(TypeError), context.call(CallKind.AGENT_STEP) as call:
        call.sent(1)  # refused before its size is measured
    with pytest.raises(RuntimeError), context.call(CallKind.AGENT_STEP) as call:
        call.sent("a")
        call.sent("b")  # sent twice
    with pytest.raises(KeyboardInterrupt), context.call(CallKind.SELF_QUERY):
        raise KeyboardInterrupt  # interrupted before sending
    with pytest.raises(ValueError), context.call(CallKind.QUERY_FUSION) as call:
        call.sent(context.protect(NAME))
        call.restore("Réponse.")
        raise ValueError(NAME)  # failed after an answer
    context.close()
    outcomes = [report.outcome for report in context.reports()]
    assert outcomes == [
        CallOutcome.COMPLETED,
        CallOutcome.COMPLETED,
        CallOutcome.COMPLETED,
        CallOutcome.PROMPT_TOO_LONG,
        CallOutcome.FAILED,
        CallOutcome.FAILED,
        CallOutcome.FAILED,
        CallOutcome.FAILED,
    ]
    assert _log_request(ctx, context) == 8
    assert len(_events(ctx)) == 8


# --- Assainissement de error_type ---------------------------------------------------------


@pytest.mark.parametrize(
    ("error_type", "logged"),
    [
        ("TimeoutError", "TimeoutError"),
        ("RuntimeError", "RuntimeError"),
        (f"Err_{NAME}", "ComponentError"),
        ("print", "ComponentError"),  # a builtin that is not an exception
        ("ComponentError", "ComponentError"),
        (42, "ComponentError"),
    ],
)
def test_the_error_type_is_sanitised(ctx, error_type, logged):  # noqa: F811
    report = outcome_reports(ctx["ids"]["employee"])[CallOutcome.FAILED]
    _log_one(ctx, replace(report, error_type=error_type))
    [event] = _events(ctx)
    assert event.details["provider_call"]["error_type"] == logged


# --- Aucune donnée sensible ----------------------------------------------------------------


def test_no_sensitive_data_in_any_column(ctx):  # noqa: F811
    for report in outcome_reports(ctx["ids"]["employee"]).values():
        _log_one(ctx, report)
    events = _events(ctx)
    assert len(events) == 4
    for event in events:
        columns = {c.name: getattr(event, c.name) for c in AuditLog.__table__.columns}
        _assert_clean(json.dumps(columns, default=str))
        assert "session" not in json.dumps(event.details)


def test_no_sensitive_data_through_the_api_and_the_export(ctx):  # noqa: F811
    for report in outcome_reports(ctx["ids"]["employee"]).values():
        _log_one(ctx, report)
    client, admin = ctx["client"], ctx["headers"]("admin")
    listed = client.get("/audit", params={"action": "LLM_CALL"}, headers=admin)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 4
    _assert_clean(listed.text)
    failures = client.get(
        "/audit", params={"action": "LLM_CALL", "result": "FAILURE"}, headers=admin
    )
    assert sorted(
        item["details"]["provider_call"]["outcome"] for item in failures.json()["items"]
    ) == [
        "CALL_LIMIT_EXCEEDED",
        "FAILED",
    ]
    exported = client.get("/audit/export", params={"action": "LLM_CALL"}, headers=admin)
    assert exported.status_code == 200
    assert len(exported.text.strip().splitlines()) == 5  # header + 4 calls
    assert '""call_kind"":""AGENT_STEP""' in exported.text
    _assert_clean(exported.text)


# --- Coexistence avec les événements llm_call ---------------------------------------------


class AnsweringProvider:
    def complete(self, prompt, *, timeout_seconds):
        return "Ok."


def _gateway_report(caller_id):
    chunk = RetrievedChunk("Procédure A.", {"source": "a.pdf", "business_layer": "layer_a"})
    try:
        result = ask(
            QUESTION,
            allowed_layers=["layer_a"],
            excluded_sources=["c3.pdf"],
            max_extracts=3,
            max_context_chars=300,
            max_prompt_chars=5000,
            timeout_seconds=10,
            retriever=lambda question: [chunk],
            assembler=lambda question, extracts: "\n".join([question, *(e.text for e in extracts)]),
            provider=AnsweringProvider(),
            known_values={EntityType.PERSON: [NAME]},
            caller_id=caller_id,
        )
    except GatewayError as error:  # the fake provider does not fail
        return error.report
    return result.report


def test_gateway_and_provider_call_events_coexist(ctx):  # noqa: F811
    with ctx["session_factory"]() as db:
        user = _user(db, ctx)
        log_gateway_call(db, user=user, report=_gateway_report(user.id), ip_address=IP)
        db.commit()
    context = request(ctx["ids"]["employee"])
    provider_call(context)
    context.close()
    _log_request(ctx, context)
    gateway, provider = _events(ctx)
    assert set(gateway.details) == {"llm_call"}
    assert (
        len(gateway.details["llm_call"]) == 11 and "request_id" not in gateway.details["llm_call"]
    )
    assert set(provider.details) == {"provider_call"}
    assert set(provider.details["provider_call"]) == PROVIDER_KEYS
    assert gateway.action == provider.action == "LLM_CALL"


# --- R5 inchangée : chaque appel fournisseur est un événement -----------------------------


def test_r5_still_counts_each_provider_call_event(ctx):  # noqa: F811
    # Documented behaviour (SEC-01): R5 is not changed at this step. 15 requests of 4
    # calls in 9 minutes reach the default floor of 60 events.
    details = {"provider_call": {"call_index": 0}}
    ctx["add"](
        AuditAction.LLM_CALL,
        60,
        user="employee",
        start=NOW - timedelta(minutes=9),
        step=timedelta(seconds=9),
        result=AuditResult.SUCCESS,
        resource_type=ResourceType.LLM_GATEWAY,
        details=details,
    )
    alerts = [alert for alert in ctx["detect"]() if alert.rule is AlertRule.ACTIVITY_SPIKE]
    assert [alert.user_id for alert in alerts] == [ctx["ids"]["employee"]]
