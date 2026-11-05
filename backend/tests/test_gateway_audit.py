# US 07 T5, étape 3 : journalisation des appels à la passerelle LLM (SEC-01).
#
# La passerelle est appelée pour de vrai avec de faux composants ; son compte rendu est
# journalisé par log_gateway_call, puis relu en base, via GET /audit et via l'export CSV.
# Valeurs synthétiques uniquement.

import ast
import json
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from test_suspicious_activity import IP, NOW, ctx  # noqa: F401  (reused fixture)

from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.user import User
from app.schemas.security_alert import AlertRule
from app.services.audit import log_gateway_call
from app.services.llm_gateway import GatewayError, GatewayReport, Outcome, Stage, ask
from app.services.pseudonymization import EntityType
from app.services.rag_context import ExclusionReason, RetrievedChunk

C3_SOURCE = "Prive__Direction_Generale_Strategie.pdf"
LAYER = "layer_a"
NAME = "Awa Ndiaye"
EMAIL = "awa.test@example.org"
QUESTION = f"Le dossier de {NAME} ({EMAIL}) est-il complet ?"
SOURCE = "Procedure_Front.pdf"
TOKEN = re.compile(r"\[(PERSONNE|CLIENT|DOSSIER)_\d+_[0-9a-f]{32}\]")
DETAIL_KEYS = {
    "outcome",
    "failed_stage",
    "error_type",
    "received",
    "retained",
    "excluded",
    "prompt_chars",
    "answer_chars",
    "tokens_restored",
    "tokens_withheld",
    "gateway_ms",
}
CHUNKS = [
    RetrievedChunk(
        f"MARQUEUR_A procédure suivie par {NAME}.",
        {"source": SOURCE, "business_layer": LAYER},
        0.9,
    ),
    RetrievedChunk("MARQUEUR_B autre couche.", {"source": "b.pdf", "business_layer": "b"}, 0.8),
    RetrievedChunk("MARQUEUR_C3 note.", {"source": C3_SOURCE, "business_layer": LAYER}, 0.7),
    RetrievedChunk(
        f"MARQUEUR_A procédure suivie par {NAME}.",
        {"source": SOURCE, "business_layer": LAYER},
        0.6,
    ),
    RetrievedChunk("MARQUEUR_LONG " + "x" * 500, {"source": SOURCE, "business_layer": LAYER}, 0.5),
]


class FakeProvider:
    def __init__(self, error=None):
        self.error = error
        self.prompts = []

    def complete(self, prompt, *, timeout_seconds):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return "Suivi par " + TOKEN.search(prompt).group() + "."


def _ask(caller_id, chunks=CHUNKS, *, max_prompt_chars=10_000, provider=None):
    """Return the report of one gateway call, whatever its outcome."""
    try:
        result = ask(
            QUESTION,
            allowed_layers=[LAYER],
            excluded_sources=[C3_SOURCE],
            max_extracts=5,
            max_context_chars=300,
            max_prompt_chars=max_prompt_chars,
            timeout_seconds=10,
            retriever=lambda question: list(chunks),
            assembler=lambda question, extracts: "\n".join(
                [question, *(extract.text for extract in extracts)]
            ),
            provider=provider or FakeProvider(),
            known_values={EntityType.PERSON: [NAME]},
            caller_id=caller_id,
        )
    except GatewayError as error:
        return error.report
    return result.report


def _log(env, report, user="employee", ip=IP):
    with env["session_factory"]() as db:
        log_gateway_call(db, user=db.get(User, env["ids"][user]), report=report, ip_address=ip)
        db.commit()


def _events(env):
    with env["session_factory"]() as db:
        return list(
            db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.LLM_CALL.value))
        )


def _outcome_reports(env):
    caller = env["ids"]["employee"]
    return {
        Outcome.ANSWERED: _ask(caller),
        Outcome.NO_AUTHORIZED_CONTEXT: _ask(caller, chunks=CHUNKS[1:3]),
        Outcome.PROMPT_TOO_LONG: _ask(caller, max_prompt_chars=10),
        Outcome.FAILED: _ask(caller, provider=FakeProvider(error=RuntimeError(NAME))),
    }


# --- Les quatre issues et leur résultat --------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "result"),
    [
        (Outcome.ANSWERED, AuditResult.SUCCESS),
        (Outcome.NO_AUTHORIZED_CONTEXT, AuditResult.SUCCESS),
        (Outcome.PROMPT_TOO_LONG, AuditResult.SUCCESS),
        (Outcome.FAILED, AuditResult.FAILURE),
    ],
)
def test_each_outcome_is_logged_as_one_event(ctx, outcome, result):  # noqa: F811
    report = _outcome_reports(ctx)[outcome]
    assert report.outcome is outcome
    _log(ctx, report)
    [event] = _events(ctx)
    assert event.action == AuditAction.LLM_CALL.value
    assert event.resource_type == ResourceType.LLM_GATEWAY.value
    assert event.resource_id is None
    assert event.result == result.value
    assert event.user_id == ctx["ids"]["employee"]
    assert event.user_department_id is not None
    assert event.resource_department_id is None  # layers are not departments (H10)
    assert event.ip_address == IP
    assert event.details["llm_call"]["outcome"] == outcome.value


def test_every_allowed_field_is_logged_and_nothing_else(ctx):  # noqa: F811
    report = _ask(ctx["ids"]["employee"])
    _log(ctx, report)
    [event] = _events(ctx)
    assert set(event.details) == {"llm_call"}
    logged = event.details["llm_call"]
    assert set(logged) == DETAIL_KEYS
    assert logged == {
        "outcome": "ANSWERED",
        "failed_stage": None,
        "error_type": None,
        "received": report.context.received,
        "retained": report.context.retained,
        "excluded": {reason.value: n for reason, n in sorted(report.context.excluded.items())},
        "prompt_chars": report.prompt_chars,
        "answer_chars": report.answer_chars,
        "tokens_restored": 1,
        "tokens_withheld": 0,
        "gateway_ms": report.duration_ms,
    }


def test_a_failure_logs_its_stage_and_a_safe_error_type(ctx):  # noqa: F811
    leaky = type(f"Err_{NAME.replace(' ', '_')}", (Exception,), {})
    for error, expected in ((TimeoutError(), "TimeoutError"), (leaky(), "ComponentError")):
        _log(ctx, _ask(ctx["ids"]["employee"], provider=FakeProvider(error=error)))
    logged = [event.details["llm_call"] for event in _events(ctx)]
    assert [(d["failed_stage"], d["error_type"]) for d in logged] == [
        ("PROVIDER", "TimeoutError"),
        ("PROVIDER", "ComponentError"),
    ]


def test_an_error_type_forged_in_the_report_is_neutralised(ctx):  # noqa: F811
    report = _ask(ctx["ids"]["employee"], provider=FakeProvider(error=RuntimeError()))
    for forged in (f"Err_{NAME}", "Exception_with_data", "ComponentErrorX", 42):
        _log(ctx, replace(report, error_type=forged))
    assert {event.details["llm_call"]["error_type"] for event in _events(ctx)} == {"ComponentError"}


# --- Comptage des extraits écartés par motif (option a) ----------------------------------


def test_discarded_extracts_are_counted_by_reason(ctx):  # noqa: F811
    _log(ctx, _ask(ctx["ids"]["employee"]))
    [event] = _events(ctx)
    logged = event.details["llm_call"]
    assert logged["excluded"] == {
        ExclusionReason.CONTEXT_BUDGET.value: 1,
        ExclusionReason.DUPLICATE.value: 1,
        ExclusionReason.EXCLUDED_SOURCE.value: 1,
        ExclusionReason.LAYER_NOT_ALLOWED.value: 1,
    }
    assert logged["received"] == 5 and logged["retained"] == 1
    assert logged["received"] == logged["retained"] + sum(logged["excluded"].values())


def test_a_failure_before_the_context_logs_no_counters(ctx):  # noqa: F811
    def broken(question):
        raise RuntimeError(NAME)

    try:
        ask(
            QUESTION,
            allowed_layers=[LAYER],
            excluded_sources=[C3_SOURCE],
            max_extracts=5,
            max_context_chars=300,
            max_prompt_chars=1000,
            timeout_seconds=10,
            retriever=broken,
            assembler=lambda q, e: q,
            provider=FakeProvider(),
            caller_id=ctx["ids"]["employee"],
        )
    except GatewayError as error:
        _log(ctx, error.report)
    [event] = _events(ctx)
    logged = event.details["llm_call"]
    assert (logged["failed_stage"], logged["received"], logged["excluded"]) == (
        "RETRIEVAL",
        None,
        {},
    )
    assert event.result == AuditResult.FAILURE.value


# --- Aucune donnée sensible --------------------------------------------------------------


SENSITIVE = (NAME, "Awa", "Ndiaye", EMAIL, "example.org", "MARQUEUR", SOURCE, C3_SOURCE, "PERSONNE")


def _assert_clean(text):
    for value in SENSITIVE:
        assert value not in text, value
    assert TOKEN.search(text) is None


def test_no_sensitive_data_in_any_column(ctx):  # noqa: F811
    for report in _outcome_reports(ctx).values():
        _log(ctx, report)
    events = _events(ctx)
    assert len(events) == 4
    for event in events:
        columns = {
            column.name: getattr(event, column.name) for column in AuditLog.__table__.columns
        }
        _assert_clean(json.dumps(columns, default=str))
        assert "question" not in json.dumps(event.details)  # no question length either (H8)


def test_no_sensitive_data_through_the_api_and_the_export(ctx):  # noqa: F811
    for report in _outcome_reports(ctx).values():
        _log(ctx, report)
    client, admin = ctx["client"], ctx["headers"]("admin")

    listed = client.get("/audit", params={"action": "LLM_CALL"}, headers=admin)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 4
    assert {item["action"] for item in items} == {"LLM_CALL"}
    assert {item["resource_type"] for item in items} == {"LLM_GATEWAY"}
    _assert_clean(listed.text)

    failures = client.get(
        "/audit", params={"action": "LLM_CALL", "result": "FAILURE"}, headers=admin
    )
    assert [item["details"]["llm_call"]["outcome"] for item in failures.json()["items"]] == [
        "FAILED"
    ]

    exported = client.get("/audit/export", params={"action": "LLM_CALL"}, headers=admin)
    assert exported.status_code == 200
    rows = exported.text.strip().splitlines()
    assert len(rows) == 5  # header + 4 calls
    assert sum("LLM_CALL" in row for row in rows) == 4
    assert '""outcome"":""ANSWERED""' in exported.text  # details exported as JSON
    _assert_clean(exported.text)


# --- caller_id et autres refus -----------------------------------------------------------


@pytest.mark.parametrize("caller", [None, "other", "admin"])
def test_a_report_of_another_caller_is_refused(ctx, caller):  # noqa: F811
    caller_id = None if caller is None else ctx["ids"][caller]
    report = _ask(caller_id)
    with pytest.raises(ValueError, match="caller_id") as error:
        _log(ctx, report, user="employee")
    assert NAME not in str(error.value)
    assert str(caller_id) not in str(error.value) or caller_id is None
    assert _events(ctx) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"caller_id": True},
        {"caller_id": "1"},
        {"outcome": "ANSWERED"},
        {"failed_stage": Stage.PROVIDER},
        {"error_type": "RuntimeError"},
        {"tokens_restored": -1},
        {"prompt_chars": "12"},
        {"duration_ms": 1.5},
    ],
)
def test_an_inconsistent_report_is_refused_without_writing(ctx, changes):  # noqa: F811
    report = replace(_ask(ctx["ids"]["employee"]), **changes)
    if "caller_id" not in changes:
        report = replace(report, caller_id=ctx["ids"]["employee"])
    with pytest.raises((TypeError, ValueError)):
        _log(ctx, report)
    assert _events(ctx) == []


def test_only_a_gateway_report_and_a_user_are_accepted(ctx):  # noqa: F811
    with ctx["session_factory"]() as db:
        user = db.get(User, ctx["ids"]["employee"])
        with pytest.raises(TypeError):
            log_gateway_call(db, user=user, report={"outcome": "ANSWERED"}, ip_address=IP)
        with pytest.raises(TypeError):
            log_gateway_call(db, user=None, report=_ask(user.id), ip_address=IP)
        with pytest.raises(TypeError):
            log_gateway_call(db, user=user, report=_ask(user.id), ip_address=12)
    assert _events(ctx) == []


# --- Détection : R1 à R4 non perturbées, R5 inclut les appels LLM -----------------------


def test_failed_llm_calls_trigger_no_access_rule(ctx):  # noqa: F811
    ctx["add"](
        AuditAction.LLM_CALL,
        40,
        user="employee",
        start=NOW - timedelta(minutes=30),
        step=timedelta(seconds=30),
        result=AuditResult.FAILURE,
        resource_type=ResourceType.LLM_GATEWAY,
        details={"llm_call": {"outcome": "FAILED"}},
    )
    rules = {alert.rule for alert in ctx["detect"]()}
    assert rules.isdisjoint(
        {
            AlertRule.LOGIN_BRUTE_FORCE,
            AlertRule.TOKEN_ABUSE,
            AlertRule.REPEATED_FORBIDDEN,
            AlertRule.CROSS_DEPARTMENT_PROBING,
            AlertRule.ADMIN_FEATURE_PROBING,
            AlertRule.DOCUMENT_ENUMERATION,
            AlertRule.SEARCH_BURST,
            AlertRule.ACTIVITY_SPIKE,  # 40 calls over 20 minutes: below the spike floor
        }
    )


def test_a_burst_of_llm_calls_is_an_activity_spike(ctx):  # noqa: F811
    ctx["add"](
        AuditAction.LLM_CALL,
        60,
        user="employee",
        start=NOW - timedelta(minutes=9),
        step=timedelta(seconds=1),
        result=AuditResult.SUCCESS,
        resource_type=ResourceType.LLM_GATEWAY,
    )
    alerts = [alert for alert in ctx["detect"]() if alert.rule is AlertRule.ACTIVITY_SPIKE]
    assert [alert.user_id for alert in alerts] == [ctx["ids"]["employee"]]
    assert alerts[0].details["actions"] == ["LLM_CALL"]


# --- Aucune migration --------------------------------------------------------------------


def test_the_new_values_fit_the_existing_columns_without_migration():
    columns = AuditLog.__table__.columns
    assert len(AuditAction.LLM_CALL.value) <= columns["action"].type.length
    assert len(ResourceType.LLM_GATEWAY.value) <= columns["resource_type"].type.length
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revisions = {}
    for path in versions.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign | ast.Assign):
                target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
                if getattr(target, "id", None) in {"revision", "down_revision"}:
                    revisions.setdefault(path.name, {})[target.id] = ast.literal_eval(node.value)
    parents = set()
    for r in revisions.values():
        down = r["down_revision"]
        parents.update(down if isinstance(down, tuple) else {down})
    heads = {r["revision"] for r in revisions.values()} - parents
    assert heads == {"433466e39163"}  # unchanged head: no new revision
    for path in versions.glob("*.py"):
        assert "LLM_CALL" not in path.read_text(encoding="utf-8")


def test_the_report_type_is_the_gateway_one():
    assert GatewayReport.__module__ == "app.services.llm_gateway"
