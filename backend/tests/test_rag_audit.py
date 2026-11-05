"""SEC-01 central audit of the RAG provider calls: route -> RagService -> log_request_calls.

End to end and offline: the real routes, RagService, LangGraph agent, retrievers,
LLMRequestContext and audit.log_request_calls; a fake provider instead of Groq, a local
in-memory Qdrant, fake embeddings, an in-memory SQLite audit journal.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

import pytest

pytest.importorskip("langchain_classic")
pytest.importorskip("llama_index.core")
pytest.importorskip("qdrant_client")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_qdrant import QdrantVectorStore as LCQdrantVectorStore
from langgraph.store.memory import InMemoryStore
from llama_index.core import MockEmbedding
from pydantic import Field, SecretStr
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import rag as rag_api
from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditLog, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User, UserRole, UserStatus
from app.services.Agentic_RAG import llm_adapters
from app.services.Agentic_RAG import retriever_tools as rt
from app.services.Agentic_RAG.Data_preparation.ingestion import CREDIT_RISQUES, FRONT_OFFICE, PRIVE
from app.services.Agentic_RAG.rag_agent import RagService
from app.services.llm_request import CallKind, LLMRequestContext, RequestLimits, require_request

TEST_DEPARTMENT = "Test Department (tests only)"
DIM = 8
COLLECTION = "rag_audit_tests"
QUESTION = "Dossier de Jean Dupont, jean.dupont@example.com, IBAN FR7630006000011234567890189 ?"
ANSWER = "Réponse finale confidentielle [1]"
EXTRACT_MARKER = "extrait-marqueur-unique"
SENSITIVE = (
    "Jean Dupont",
    "jean.dupont@example.com",
    "30006000011234567890189",
    ANSWER,
    EXTRACT_MARKER,
    "alpha.pdf",
    "procedure",
)
PROVIDER_CALL_KEYS = {
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
SELF_QUERY_REPLY = '```json\n{"query": "procedure", "filter": "NO_FILTER"}\n```'


class FakeProvider(BaseChatModel):
    replies: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)
    delay: float = 0.0
    error: Exception | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-provider"

    def _reply(self):
        self.calls.append(require_request().request_id)
        if self.error is not None:
            raise self.error
        reply = self.replies.pop(0)
        message = AIMessage(content=reply) if isinstance(reply, str) else reply
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._reply()

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._reply()


def _tool_call(name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"query": "procedure"}, "id": f"c-{name}"}],
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Real route + RagService + log_request_calls; fake provider, Qdrant and SQLite."""
    monkeypatch.chdir(tmp_path)  # any relative logs/audit.jsonl would land here
    monkeypatch.setattr(rt, "DEPARTMENT_LAYERS", {TEST_DEPARTMENT: FRONT_OFFICE})
    provider = FakeProvider()
    monkeypatch.setattr(llm_adapters, "_provider", lambda: provider)
    # Decision H1 granted with an offline fake key: ChatGroq binds the agent tools.
    monkeypatch.setattr(settings, "allow_external_llm", True)
    monkeypatch.setattr(settings, "llm_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_fake_offline"))
    llm_adapters._groq.cache_clear()

    client = QdrantClient(":memory:")
    client.create_collection(
        COLLECTION, vectors_config=qm.VectorParams(size=DIM, distance=qm.Distance.COSINE)
    )
    points = []
    for name, layer in {
        "alpha": FRONT_OFFICE,
        "bravo": FRONT_OFFICE,
        "charlie": CREDIT_RISQUES,
    }.items():
        meta = {"source": f"{name}.pdf", "page": 1, "doc_type": PRIVE, "business_layer": layer}
        points.append(
            qm.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
                vector=[0.5] * DIM,
                payload={
                    "text": f"procedure {name} {EXTRACT_MARKER}",
                    **meta,
                    "metadata": dict(meta),
                },
            )
        )
    client.upsert(COLLECTION, points)
    monkeypatch.setattr(rt, "get_embed_model", lambda: MockEmbedding(embed_dim=DIM))
    lc_store = LCQdrantVectorStore(
        client=client,
        collection_name=COLLECTION,
        embedding=rt.LlamaIndexEmbeddings(),
        content_payload_key="text",
        metadata_payload_key="metadata",
    )
    monkeypatch.setattr(rt, "COLLECTION_NAME", COLLECTION)
    monkeypatch.setattr(rt, "_client", lambda: client)
    monkeypatch.setattr(rt, "_lc_vectorstore", lambda: lc_store)
    monkeypatch.setattr(settings, "max_provider_calls", 8)
    monkeypatch.setattr(settings, "timeout_seconds", 5.0)
    for clear in (rt._llama_index.cache_clear, rt._retriever_cache.clear, rt._bm25_cache.clear):
        clear()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with Session() as db:
        department = Department(name=TEST_DEPARTMENT)
        db.add(department)
        db.flush()
        user = User(
            email="analyste@example.com",
            password_hash=hash_password("Password123"),
            status=UserStatus.ACTIVE,
            role=UserRole.EMPLOYEE,
            department_id=department.id,
        )
        db.add(user)
        db.commit()
        user_id, department_id = user.id, department.id

    service = RagService(store=InMemoryStore())
    service._build_agent()
    api = FastAPI()
    api.include_router(rag_api.router)

    def override_get_db():
        with Session() as db:
            yield db

    api.dependency_overrides[get_db] = override_get_db
    api.dependency_overrides[rag_api.get_rag_service] = lambda: service
    token = create_access_token(subject=str(user_id), role="EMPLOYEE")

    def events():
        with Session() as db:
            return db.scalars(select(AuditLog).order_by(AuditLog.id)).all()

    with TestClient(api) as http:
        yield {
            "http": http,
            "headers": {"Authorization": f"Bearer {token}"},
            "provider": provider,
            "service": service,
            "events": events,
            "session": Session,
            "user_id": user_id,
            "department_id": department_id,
            "tmp": tmp_path,
        }
    for clear in (rt._llama_index.cache_clear, rt._retriever_cache.clear, rt._bm25_cache.clear):
        clear()
    llm_adapters._groq.cache_clear()


def _ask(env, question=QUESTION, path="/rag/chat"):
    return env["http"].post(path, json={"question": question}, headers=env["headers"])


def _calls(env) -> list[dict]:
    return [event.details["provider_call"] for event in env["events"]()]


def _assert_audit_safe(env):
    """Every event is an LLM_CALL with the closed list of keys, without any content."""
    for event in env["events"]():
        assert event.action == AuditAction.LLM_CALL.value
        assert event.resource_type == ResourceType.LLM_GATEWAY.value
        assert event.user_id == env["user_id"]
        assert event.user_department_id == env["department_id"]
        assert set(event.details) == {"provider_call"}
        assert set(event.details["provider_call"]) == PROVIDER_CALL_KEYS
        assert event.details["provider_call"]["prompt_chars"] is None  # decision H8
        row = json.dumps(
            {c.name: getattr(event, c.name) for c in AuditLog.__table__.columns},
            default=str,
            ensure_ascii=False,
        )
        for value in SENSITIVE:
            assert value not in row, value


# --- All the reports of a request reach the central journal ----------------------------


def test_nested_calls_of_one_request_are_all_audited(env):
    env["provider"].replies = [_tool_call("search_multi_query"), "procedure alpha", ANSWER]
    response = _ask(env)
    assert response.status_code == 200 and response.json()["answer"] == ANSWER

    calls = _calls(env)
    assert [c["call_kind"] for c in calls] == ["AGENT_STEP", "QUERY_REWRITE", "AGENT_STEP"]
    assert [c["call_index"] for c in calls] == [0, 1, 2]
    assert len({c["request_id"] for c in calls}) == 1
    assert {c["request_id"] for c in calls} == set(env["provider"].calls)
    assert all(c["outcome"] == "COMPLETED" for c in calls)
    assert all(e.result == AuditResult.SUCCESS.value for e in env["events"]())
    _assert_audit_safe(env)


def test_the_four_kinds_are_audited_when_they_run(env):
    env["provider"].replies = [
        _tool_call("search_multi_query"),
        "procedure alpha",
        _tool_call("search_self_query"),
        SELF_QUERY_REPLY,
        _tool_call("search_fusion"),
        "procedure alpha\nprocedure bravo\nprocedure",
        ANSWER,
    ]
    assert _ask(env).status_code == 200
    kinds = [c["call_kind"] for c in _calls(env)]
    assert set(kinds) == {k.value for k in CallKind}
    assert kinds == [
        "AGENT_STEP",
        "QUERY_REWRITE",
        "AGENT_STEP",
        "SELF_QUERY",
        "AGENT_STEP",
        "QUERY_FUSION",
        "AGENT_STEP",
    ]
    assert [c["call_index"] for c in _calls(env)] == list(range(7))
    _assert_audit_safe(env)


def test_the_stream_route_audits_its_calls(env):
    env["provider"].replies = [_tool_call("search_bm25"), ANSWER]
    with env["http"].stream(
        "POST", "/rag/chat/stream", json={"question": QUESTION}, headers=env["headers"]
    ) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200 and '"done"' in body
    assert [c["call_kind"] for c in _calls(env)] == ["AGENT_STEP", "AGENT_STEP"]
    _assert_audit_safe(env)


# --- Errors are audited too, without their content -------------------------------------


def test_a_provider_error_is_audited_without_its_message(env):
    secret = "upstream echoed jean.dupont@example.com and Jean Dupont"
    env["provider"].error = RuntimeError(secret)
    response = _ask(env)
    assert response.status_code == 500
    assert "jean.dupont" not in response.text
    (call,) = _calls(env)
    assert (call["call_kind"], call["outcome"], call["error_type"]) == (
        "AGENT_STEP",
        "FAILED",
        "RuntimeError",
    )
    assert env["events"]()[0].result == AuditResult.FAILURE.value
    assert "upstream" not in json.dumps([e.details for e in env["events"]()])
    _assert_audit_safe(env)


def test_a_provider_timeout_is_audited(env, monkeypatch):
    monkeypatch.setattr(settings, "timeout_seconds", 0.05)
    env["provider"].replies = ["too late"]
    env["provider"].delay = 1.0
    assert _ask(env).status_code == 504
    (call,) = _calls(env)
    assert (call["outcome"], call["error_type"]) == ("FAILED", "TimeoutError")
    _assert_audit_safe(env)


def test_the_call_limit_is_audited_safely(env, monkeypatch):
    monkeypatch.setattr(settings, "max_provider_calls", 1)
    env["provider"].replies = [_tool_call("search_multi_query"), "never", "never"]
    response = _ask(env)
    assert response.status_code == 500
    calls = _calls(env)
    assert [(c["call_kind"], c["outcome"]) for c in calls] == [
        ("AGENT_STEP", "COMPLETED"),
        ("QUERY_REWRITE", "CALL_LIMIT_EXCEEDED"),
        ("AGENT_STEP", "CALL_LIMIT_EXCEEDED"),
    ]
    refused = [e for e in env["events"]() if e.details["provider_call"]["call_index"] > 0]
    assert all(e.result == AuditResult.FAILURE.value for e in refused)
    for call in calls[1:]:  # nothing was sent: no size, no token, no answer
        assert (call["answer_chars"], call["tokens_sent"], call["tokens_restored"]) == (None, 0, 0)
    assert len(env["provider"].calls) == 1
    _assert_audit_safe(env)


# --- No provider call, no event; no parallel journal -----------------------------------


def test_a_request_refused_before_the_agent_writes_no_provider_event(env):
    assert _ask(env, question="   ").status_code == 422  # InvalidRequest, no context opened
    assert env["events"]() == [] and env["provider"].calls == []


def test_an_agent_failure_before_any_provider_call_writes_no_event(env, monkeypatch):
    class BrokenAgent:
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("graph failure before the model")

    monkeypatch.setattr(env["service"], "agent", BrokenAgent())
    assert _ask(env).status_code == 500
    assert env["events"]() == [] and env["provider"].calls == []


def test_no_parallel_rag_journal_is_written(env):
    env["provider"].replies = [_tool_call("search_multi_query"), "procedure alpha", ANSWER]
    assert _ask(env).status_code == 200
    assert not (env["tmp"] / "logs").exists()
    rag_audit = logging.getLogger("rag.audit")
    assert not any(isinstance(h, logging.FileHandler) for h in rag_audit.handlers)


def test_no_parallel_journal_remains_in_the_rag_code():
    rag_dir = Path(rt.__file__).parent
    files = [*rag_dir.glob("*.py"), Path(rag_api.__file__)]
    for path in files:
        source = path.read_text(encoding="utf-8")
        for forbidden in ("log_event", "setup_audit_logging", "audit.jsonl", "rag.audit"):
            assert forbidden not in source, f"{path.name}: {forbidden}"


def test_the_central_audit_refuses_a_context_of_another_user(env):
    """The hook only writes through log_request_calls, which checks the caller first."""
    context = LLMRequestContext(
        caller_id=env["user_id"] + 1,
        limits=RequestLimits(max_provider_calls=1, max_prompt_chars=10, timeout_seconds=1.0),
    )
    context.close()

    class NoClient:
        client = None

    with env["session"]() as db:
        hook = rag_api._central_audit(db, db.get(User, env["user_id"]), NoClient())
        with pytest.raises(ValueError):
            hook(context)
    assert env["events"]() == []


def test_route_sources_stay_in_the_user_scope(env):
    """SEC-02 end to end: the real route, agent and retrievers never return another layer."""
    env["provider"].replies = [_tool_call("search_multi_query"), "procedure", ANSWER]
    response = _ask(env)
    assert response.status_code == 200
    sources = {s["source"] for s in response.json()["sources"]}
    assert sources and sources <= {"alpha.pdf", "bravo.pdf"}  # charlie is credit_risques
    for source in response.json()["sources"]:  # classification never returned (T4)
        assert source.get("doc_type") is None and source.get("business_layer") is None
