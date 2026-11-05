"""SEC-01 for the RAG agent: the four provider calls go through LLMRequestContext.

Offline: a fake provider replaces Groq (``llm_adapters._provider``), local in-memory
Qdrant, fake embeddings. Needs the "rag" dependency group; skipped otherwise.
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("langchain_classic")
pytest.importorskip("llama_index.core")
pytest.importorskip("qdrant_client")

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_qdrant import QdrantVectorStore as LCQdrantVectorStore
from langgraph.store.memory import InMemoryStore
from llama_index.core import MockEmbedding
from llama_index.core import Settings as LlamaSettings
from pydantic import Field, SecretStr
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.core.config import settings
from app.services.Agentic_RAG import llm_adapters
from app.services.Agentic_RAG import retriever_tools as rt
from app.services.Agentic_RAG.Data_preparation.ingestion import CREDIT_RISQUES, FRONT_OFFICE, PRIVE
from app.services.Agentic_RAG.llm_adapters import (
    GatewayChatModel,
    GatewayLlamaLLM,
    ProviderCallError,
)
from app.services.Agentic_RAG.rag_agent import RagError, RagService, RagTimeout
from app.services.llm_request import (
    CallKind,
    CallLimitExceeded,
    CallOutcome,
    LLMRequestContext,
    NoActiveRequest,
    PromptTooLong,
    RequestLimits,
    require_request,
)
from app.services.pseudonymization import EntityType
from app.services.token_restore import ANY_TOKEN

TEST_DEPARTMENT = "Test Department (tests only)"
DIM = 8
COLLECTION = "rag_adapter_tests"
SECRET_QUESTION = "Dossier de Jean Dupont, email jean.dupont@example.com : procédure ?"
FOREIGN_TOKEN = "[PERSONNE_9_" + "0" * 32 + "]"


class FakeProvider(BaseChatModel):
    """Records exactly what would reach Groq; answers from a script."""

    replies: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)
    delay: float = 0.0
    error: Exception | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-provider"

    def _reply(self, messages, kwargs) -> ChatResult:
        self.calls.append(
            {"messages": messages, "kwargs": kwargs, "request_id": require_request().request_id}
        )
        if self.error is not None:
            raise self.error
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, str):
            reply = AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._reply(messages, kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self._reply(messages, kwargs)


@pytest.fixture
def groq_consent(monkeypatch):
    """Decision H1 granted, with an offline fake key: ChatGroq can be built, never called."""
    monkeypatch.setattr(settings, "allow_external_llm", True)
    monkeypatch.setattr(settings, "llm_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_fake_offline"))
    llm_adapters._groq.cache_clear()
    yield
    llm_adapters._groq.cache_clear()


@pytest.fixture
def provider(monkeypatch, groq_consent):
    fake = FakeProvider()
    monkeypatch.setattr(llm_adapters, "_provider", lambda: fake)
    return fake


def _context(max_calls=5, timeout=2.0, known=("Jean Dupont", "Marie Curie")):
    return LLMRequestContext(
        caller_id=1,
        limits=RequestLimits(
            max_provider_calls=max_calls, max_prompt_chars=50_000, timeout_seconds=timeout
        ),
        known_values={EntityType.PERSON: list(known)},
    )


def _sent_text(call) -> str:
    """Everything the fake provider received, as one text."""
    parts = []
    for message in call["messages"]:
        parts.append(json.dumps(message.content, ensure_ascii=False))
        for tool in getattr(message, "tool_calls", []):
            parts.append(json.dumps(tool["args"], ensure_ascii=False))
    return "\n".join(parts)


def _kinds_spy(monkeypatch):
    """Records (context, kind) for every LLMRequestContext.call."""
    seen = []
    original = LLMRequestContext.call

    def spy(self, kind):
        seen.append((self, kind))
        return original(self, kind)

    monkeypatch.setattr(LLMRequestContext, "call", spy)
    return seen


# --- Fail closed without a request context -----------------------------------------


def test_no_active_request_means_no_provider_call(provider):
    with pytest.raises(NoActiveRequest):
        GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("question")
    with pytest.raises(NoActiveRequest):
        GatewayLlamaLLM().complete("question")
    assert provider.calls == []


# --- Each kind goes through the context -----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "call"),
    [
        (CallKind.AGENT_STEP, lambda: GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("q")),
        (CallKind.QUERY_REWRITE, lambda: rt._lc_llm(CallKind.QUERY_REWRITE).invoke("q")),
        (CallKind.SELF_QUERY, lambda: rt._lc_llm(CallKind.SELF_QUERY).invoke("q")),
        (CallKind.QUERY_FUSION, lambda: rt._llama_llm().complete("q")),
    ],
)
def test_each_kind_is_one_reported_call_of_the_context(provider, kind, call):
    provider.replies = ["ok"]
    with _context() as context:
        call()
    (report,) = context.reports()
    assert (report.kind, report.outcome, report.call_index) == (kind, CallOutcome.COMPLETED, 0)
    assert report.request_id == context.request_id == provider.calls[0]["request_id"]


# --- Limits: max_provider_calls and timeout -------------------------------------------


def test_max_provider_calls_is_refused_without_provider_call(provider):
    provider.replies = ["first", "second"]
    model = GatewayChatModel(kind=CallKind.AGENT_STEP)
    with _context(max_calls=1) as context:
        model.invoke("q")
        with pytest.raises(CallLimitExceeded):
            model.invoke("q")
    assert len(provider.calls) == 1
    outcomes = [r.outcome for r in context.reports()]
    assert outcomes == [CallOutcome.COMPLETED, CallOutcome.CALL_LIMIT_EXCEEDED]


def test_the_context_timeout_is_passed_to_the_provider(provider):
    provider.replies = ["sync", "async"]
    model = GatewayChatModel(kind=CallKind.AGENT_STEP)
    with _context(timeout=7.5):
        model.invoke("q")
        asyncio.run(model.ainvoke("q"))
    assert [c["kwargs"]["timeout"] for c in provider.calls] == [7.5, 7.5]


def test_an_async_call_over_the_timeout_is_cut_and_reported(provider):
    provider.replies = ["too late"]
    provider.delay = 1.0
    with _context(timeout=0.05) as context, pytest.raises(TimeoutError):
        asyncio.run(GatewayChatModel(kind=CallKind.AGENT_STEP).ainvoke("q"))
    (report,) = context.reports()
    assert (report.outcome, report.error_type) == (CallOutcome.FAILED, "TimeoutError")


def test_a_provider_error_is_replaced_without_its_message(provider):
    provider.error = RuntimeError("secret: jean.dupont@example.com")
    with _context() as context, pytest.raises(ProviderCallError) as caught:
        GatewayChatModel(kind=CallKind.SELF_QUERY).invoke("q")
    assert "jean.dupont" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    (report,) = context.reports()
    assert (report.outcome, report.error_type) == (CallOutcome.FAILED, "RuntimeError")


# --- Protection before the provider, restoration of the sent tokens only --------------


def test_everything_sent_is_protected_before_the_provider(provider):
    provider.replies = ["ok"]
    messages = [
        SystemMessage("Consignes pour Jean Dupont"),
        HumanMessage(SECRET_QUESTION),
        AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {"query": "Jean Dupont"}, "id": "c1"}],
        ),
        ToolMessage("Extrait : contacter jean.dupont@example.com", tool_call_id="c1"),
    ]
    with _context():
        GatewayChatModel(kind=CallKind.AGENT_STEP).invoke(messages)
    sent = _sent_text(provider.calls[0])
    assert "Jean Dupont" not in sent and "jean.dupont@example.com" not in sent
    assert "[EMAIL]" in sent and ANY_TOKEN.search(sent)


def test_only_the_tokens_sent_by_this_call_are_restored(provider):
    with _context() as context:
        not_sent = context.protect("Marie Curie")  # in the vault, never sent by this call

        def answer(messages):
            (sent,) = ANY_TOKEN.findall(messages[0].content)
            return AIMessage(
                content=f"{sent} / {not_sent} / {FOREIGN_TOKEN}",
                tool_calls=[{"name": "search", "args": {"query": sent}, "id": "c1"}],
            )

        provider.replies = [answer]
        result = GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("Le dossier de Jean Dupont")
    assert result.content == f"Jean Dupont / {not_sent} / {FOREIGN_TOKEN}"
    assert result.tool_calls[0]["args"] == {"query": "Jean Dupont"}
    (report,) = context.reports()
    assert (report.tokens_sent, report.tokens_restored, report.tokens_withheld) == (1, 2, 2)


def test_reasoning_and_extra_provider_fields_are_dropped(provider):
    provider.replies = [AIMessage(content="ok", additional_kwargs={"reasoning_content": "x"})]
    with _context():
        result = GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("q")
    assert result.additional_kwargs == {}


# --- Sync, async and streaming --------------------------------------------------------


def test_sync_async_and_streaming_give_the_same_restored_answer(provider):
    def answer(messages):
        (token,) = ANY_TOKEN.findall(messages[0].content)
        return AIMessage(
            content=f"Réponse pour {token}",
            tool_calls=[{"name": "search", "args": {"query": token}, "id": "c1"}],
        )

    provider.replies = [answer] * 4
    model = GatewayChatModel(kind=CallKind.AGENT_STEP)

    async def async_paths():
        result = await model.ainvoke("Jean Dupont")
        chunks = [chunk async for chunk in model.astream("Jean Dupont")]
        return result, chunks

    with _context():
        sync_result = model.invoke("Jean Dupont")
        sync_chunks = list(model.stream("Jean Dupont"))
        async_result, async_chunks = asyncio.run(async_paths())
    aggregates = []
    for chunks in (sync_chunks, async_chunks):
        # One content chunk, after restoration (langchain-core adds an empty "last" chunk).
        assert len([c for c in chunks if c.content or c.tool_call_chunks]) == 1
        aggregate = chunks[0]
        for chunk in chunks[1:]:
            aggregate += chunk
        aggregates.append(aggregate)
    for result in (sync_result, async_result, *aggregates):
        assert result.content == "Réponse pour Jean Dupont"
        assert result.tool_calls[0]["args"] == {"query": "Jean Dupont"}


def test_the_llamaindex_adapter_streams_the_restored_completion(provider):
    provider.replies = [lambda m: ANY_TOKEN.findall(m[0].content)[0], "x"]
    with _context() as context:
        (response,) = list(GatewayLlamaLLM().stream_complete("Jean Dupont"))
        asyncio.run(GatewayLlamaLLM().acomplete("q"))
    assert response.text == "Jean Dupont"
    assert [r.kind for r in context.reports()] == [CallKind.QUERY_FUSION] * 2


# --- Provider: Groq only, from Settings ------------------------------------------------


def test_the_provider_is_groq_built_from_settings(monkeypatch, groq_consent):
    monkeypatch.setattr(settings, "llm_model", "groq:openai/gpt-oss-120b")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_fake_offline"))
    try:
        client = llm_adapters._provider()
        assert type(client).__name__ == "ChatGroq"
        assert client.model_name == "openai/gpt-oss-120b"  # raw Groq identifier
        assert client.groq_api_key.get_secret_value() == "gsk_fake_offline"
        assert client.max_retries == 0  # one call = one provider request
        assert client.temperature <= 1e-8  # langchain-groq turns 0 into 1e-08
    finally:
        llm_adapters._groq.cache_clear()


def test_no_direct_provider_client_outside_the_adapters():
    app_dir = Path(llm_adapters.__file__).parents[2]
    pattern = re.compile(
        r"ChatGroq\(|langchain_groq|llama_index\.llms|anthropic|Anthropic|openai|ChatOpenAI"
    )
    offenders = [
        f"{path.relative_to(app_dir)}:{n}"
        for path in app_dir.rglob("*.py")
        if path.name != "llm_adapters.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


# --- End to end through RagService: one context per request ---------------------------

CORPUS = {"alpha": FRONT_OFFICE, "bravo": FRONT_OFFICE, "charlie": CREDIT_RISQUES}


@pytest.fixture
def rag(monkeypatch, groq_consent):
    """RagService with a real agent graph, in-memory Qdrant, fake provider and embeddings."""
    monkeypatch.setattr(rt, "DEPARTMENT_LAYERS", {TEST_DEPARTMENT: FRONT_OFFICE})
    client = QdrantClient(":memory:")
    client.create_collection(
        COLLECTION, vectors_config=qm.VectorParams(size=DIM, distance=qm.Distance.COSINE)
    )
    points = []
    for name, layer in CORPUS.items():
        meta = {"source": f"{name}.pdf", "page": 1, "doc_type": PRIVE, "business_layer": layer}
        points.append(
            qm.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
                vector=[0.5] * DIM,
                payload={"text": f"procedure {name} reglement", **meta, "metadata": dict(meta)},
            )
        )
    client.upsert(COLLECTION, points)
    embed = MockEmbedding(embed_dim=DIM)
    monkeypatch.setattr(rt, "get_embed_model", lambda: embed)  # before any embedding call
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
    monkeypatch.setattr(settings, "max_provider_calls", 6)
    monkeypatch.setattr(settings, "timeout_seconds", 5.0)
    for cache in (rt._llama_index.cache_clear, rt._retriever_cache.clear, rt._bm25_cache.clear):
        cache()
    service = RagService(store=InMemoryStore())
    service._build_agent()
    yield service, rt.UserContext(user_id=42, department=TEST_DEPARTMENT)
    for cache in (rt._llama_index.cache_clear, rt._retriever_cache.clear, rt._bm25_cache.clear):
        cache()


def _with_known_names(monkeypatch):
    """The RAG has no source of known values yet: declare one, as a caller would."""

    def request_context(user):
        return LLMRequestContext(
            caller_id=user.user_id,
            limits=RequestLimits(
                max_provider_calls=settings.max_provider_calls,
                max_prompt_chars=settings.max_prompt_chars,
                timeout_seconds=settings.timeout_seconds,
            ),
            known_values={EntityType.PERSON: ["Jean Dupont"]},
        )

    monkeypatch.setattr(RagService, "_request_context", staticmethod(request_context))


def _tool_call(name: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": {"query": "procedure"}, "id": "c1"}]
    )


SELF_QUERY_REPLY = '```json\n{"query": "procedure", "filter": "NO_FILTER"}\n```'


@pytest.mark.parametrize(
    ("tool", "nested_kind", "nested_reply"),
    [
        ("search_multi_query", CallKind.QUERY_REWRITE, "procedure alpha\nreglement bravo"),
        ("search_self_query", CallKind.SELF_QUERY, SELF_QUERY_REPLY),
        ("search_fusion", CallKind.QUERY_FUSION, "procedure alpha\nreglement bravo\nprocedure"),
    ],
)
def test_one_request_one_context_for_the_agent_and_its_nested_calls(
    rag, provider, monkeypatch, caplog, tool, nested_kind, nested_reply
):
    service, user = rag
    _with_known_names(monkeypatch)
    seen = _kinds_spy(monkeypatch)
    provider.replies = [_tool_call(tool), nested_reply, "Réponse finale [1]"]
    caplog.set_level(logging.DEBUG)

    answer = asyncio.run(service.ask(user, SECRET_QUESTION))

    assert answer.answer == "Réponse finale [1]"
    assert {s["source"] for s in answer.sources} <= {"alpha.pdf", "bravo.pdf"}
    assert answer.sources  # the context went through rag_context.build_context
    contexts = {id(context) for context, _ in seen}
    assert len(contexts) == 1  # never one context per tool or per nested call
    assert [kind for _, kind in seen] == [CallKind.AGENT_STEP, nested_kind, CallKind.AGENT_STEP]
    (context, _) = seen[0]
    assert context.closed and context.caller_id == 42
    assert {c["request_id"] for c in provider.calls} == {context.request_id}
    assert all(r.outcome is CallOutcome.COMPLETED for r in context.reports())
    for call in provider.calls:
        assert "Jean Dupont" not in _sent_text(call)
        assert "jean.dupont@example.com" not in _sent_text(call)
    assert "jean.dupont" not in caplog.text and "Jean Dupont" not in caplog.text


def test_the_real_request_context_masks_before_the_provider(rag, provider):
    service, user = rag
    provider.replies = ["ok"]
    asyncio.run(service.ask(user, SECRET_QUESTION))
    sent = _sent_text(provider.calls[0])
    assert "jean.dupont@example.com" not in sent and "[EMAIL]" in sent
    # Without known values (none in the RAG yet), T3 does not pseudonymise names.
    assert "Jean Dupont" in sent


def test_two_requests_get_two_contexts(rag, provider, monkeypatch):
    service, user = rag
    seen = _kinds_spy(monkeypatch)
    provider.replies = ["un", "deux"]
    asyncio.run(service.ask(user, "q1", "s1"))
    asyncio.run(service.ask(user, "q2", "s2"))
    assert len({id(context) for context, _ in seen}) == 2
    assert len({c["request_id"] for c in provider.calls}) == 2


def test_the_request_call_limit_stops_the_agent(rag, provider, monkeypatch):
    service, user = rag
    monkeypatch.setattr(settings, "max_provider_calls", 1)
    provider.replies = [_tool_call("search_multi_query"), "never", "never"]
    with pytest.raises(RagError):
        asyncio.run(service.ask(user, "procedure"))
    assert len(provider.calls) == 1  # the nested rewrite and the next step were refused


def test_the_request_timeout_reaches_the_provider_call(rag, provider, monkeypatch):
    service, user = rag
    monkeypatch.setattr(settings, "timeout_seconds", 0.05)
    provider.replies = ["too late"]
    provider.delay = 1.0
    with pytest.raises(RagTimeout):
        asyncio.run(service.ask(user, "procedure"))


def test_stream_uses_one_context_and_streams_the_restored_answer(rag, provider, monkeypatch):
    service, user = rag
    _with_known_names(monkeypatch)
    seen = _kinds_spy(monkeypatch)

    def answer(messages):
        tokens = ANY_TOKEN.findall(" ".join(str(m.content) for m in messages))
        return f"Réponse pour {tokens[0]}"

    provider.replies = [_tool_call("search_bm25"), answer]

    async def consume():
        return [event async for event in service.stream(user, SECRET_QUESTION)]

    events = asyncio.run(consume())
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert text == "Réponse pour Jean Dupont"  # restored before being streamed
    assert not ANY_TOKEN.search(text)
    assert [e["type"] for e in events][-2:] == ["sources", "done"]
    assert len({id(context) for context, _ in seen}) == 1
    assert [kind for _, kind in seen] == [CallKind.AGENT_STEP, CallKind.AGENT_STEP]


def test_fusion_never_uses_a_default_llamaindex_llm(rag):
    scope = rt.resolve_scope(rag[1])
    fusion = rt.build_fusion_retriever(scope)
    assert type(fusion._llm) is GatewayLlamaLLM
    assert LlamaSettings._llm is None  # the OpenAI default was never resolved


# --- Groq only, behind decision H1 (allow_external_llm) -------------------------------


def test_without_external_consent_nothing_is_built_bound_or_sent(provider, monkeypatch):
    monkeypatch.setattr(settings, "allow_external_llm", False)

    def no_client(*args, **kwargs):
        raise AssertionError("ChatGroq must not be built without allow_external_llm")

    monkeypatch.setattr(llm_adapters, "ChatGroq", no_client)
    llm_adapters._groq.cache_clear()
    model = GatewayChatModel(kind=CallKind.AGENT_STEP)
    with _context() as context:
        for call in (
            lambda: model.invoke("Jean Dupont"),
            lambda: asyncio.run(model.ainvoke("q")),
            lambda: list(model.stream("q")),
            lambda: GatewayLlamaLLM().complete("q"),
            lambda: asyncio.run(GatewayLlamaLLM().acomplete("q")),
            lambda: model.bind_tools([]),
        ):
            with pytest.raises(llm_adapters.ExternalProviderRefused):
                call()
    assert provider.calls == []
    assert context.reports() == ()  # a refused call is never counted nor audited


def test_the_real_provider_functions_refuse_without_consent(groq_consent, monkeypatch):
    """No fake provider here: the real _provider() and _groq() of the module."""
    monkeypatch.setattr(settings, "allow_external_llm", False)
    llm_adapters._groq.cache_clear()
    for build in (llm_adapters._provider, llm_adapters._groq):
        with pytest.raises(llm_adapters.ExternalProviderRefused):
            build()
    # A client built while the consent was given is not reused once it is withdrawn.
    monkeypatch.setattr(settings, "allow_external_llm", True)
    assert type(llm_adapters._provider()).__name__ == "ChatGroq"
    monkeypatch.setattr(settings, "allow_external_llm", False)
    with pytest.raises(llm_adapters.ExternalProviderRefused):
        llm_adapters._provider()


def test_the_consent_is_checked_before_the_request_context(provider, monkeypatch):
    monkeypatch.setattr(settings, "allow_external_llm", False)
    with pytest.raises(llm_adapters.ExternalProviderRefused):  # not NoActiveRequest
        GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("q")


def test_tools_are_bound_by_chatgroq_itself(groq_consent, monkeypatch):
    from langchain_core.tools import tool
    from langchain_groq import ChatGroq

    @tool
    def search_bm25(query: str) -> str:
        """Recherche lexicale."""
        return ""

    @tool
    def memory_save(content: str) -> str:
        """Mémoire."""
        return ""

    seen = []
    native_bind = ChatGroq.bind_tools

    def spy(self, tools, **kwargs):
        seen.append(kwargs)
        return native_bind(self, tools, **kwargs)

    monkeypatch.setattr(ChatGroq, "bind_tools", spy)
    reference = ChatGroq(model="m", api_key="gsk_fake_offline")
    model = GatewayChatModel(kind=CallKind.AGENT_STEP)
    for options in ({}, {"tool_choice": "any"}, {"tool_choice": "search_bm25"}, {"strict": True}):
        bound = model.bind_tools([search_bm25, memory_save], **options)
        expected = native_bind(reference, [search_bm25, memory_save], **options).kwargs
        assert dict(bound.kwargs) == dict(expected)
        assert bound.bound is model  # the calls still go through the adapter
    assert len(seen) == 4


def test_max_prompt_chars_is_enforced_before_the_provider(provider):
    provider.replies = ["never"]
    context = LLMRequestContext(
        caller_id=1,
        limits=RequestLimits(max_provider_calls=5, max_prompt_chars=10, timeout_seconds=2.0),
    )
    with context, pytest.raises(PromptTooLong):
        GatewayChatModel(kind=CallKind.AGENT_STEP).invoke("une question bien trop longue")
    assert provider.calls == []
    (report,) = context.reports()
    assert (report.outcome, report.tokens_sent) == (CallOutcome.PROMPT_TOO_LONG, 0)


class _FakeGroqCompletions:
    """Stands for groq.Groq().chat.completions: records what ChatGroq really sends."""

    def __init__(self, answer):
        self.requests = []
        self._answer = answer

    def create(self, **params):
        self.requests.append(params)
        return self._answer(params)


class _FakeAsyncGroqCompletions(_FakeGroqCompletions):
    async def create(self, **params):
        return super().create(**params)


def _groq_completion(content, tool_calls=()):
    from groq.types.chat import ChatCompletion

    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-offline",
            "object": "chat.completion",
            "created": 0,
            "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_the_request_really_goes_through_chatgroq(groq_consent, monkeypatch, mode):
    """No fake provider: the real ChatGroq, down to the Groq SDK client (offline)."""
    from langchain_core.tools import tool

    @tool
    def search(query: str) -> str:
        """Recherche documentaire."""
        return ""

    def answer(params):
        sent = json.dumps(params["messages"], ensure_ascii=False)
        (token,) = set(ANY_TOKEN.findall(sent))
        call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "search", "arguments": json.dumps({"query": token})},
        }
        return _groq_completion(f"Dossier de {token}", [call])

    client = llm_adapters._groq()
    fake = (_FakeGroqCompletions if mode == "sync" else _FakeAsyncGroqCompletions)(answer)
    monkeypatch.setattr(client, "client" if mode == "sync" else "async_client", fake)
    model = GatewayChatModel(kind=CallKind.AGENT_STEP, max_tokens=321).bind_tools([search])
    history = [
        HumanMessage("Dossier de Jean Dupont ?"),
        AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {"query": "Jean Dupont"}, "id": "c0"}],
        ),
        ToolMessage("Extrait : jean.dupont@example.com", tool_call_id="c0"),
    ]
    with _context(timeout=4.5) as context:
        result = model.invoke(history) if mode == "sync" else asyncio.run(model.ainvoke(history))

    (params,) = fake.requests
    assert params["model"] == "openai/gpt-oss-120b"
    assert (params["timeout"], params["max_tokens"]) == (4.5, 321)
    assert params["tools"][0]["function"]["name"] == "search"  # formatted by ChatGroq
    assistant = params["messages"][1]
    assert assistant["tool_calls"][0]["type"] == "function"  # Groq's native format
    sent = json.dumps(params["messages"], ensure_ascii=False)
    assert "Jean Dupont" not in sent and "jean.dupont@example.com" not in sent
    assert result.content == "Dossier de Jean Dupont"  # restored
    assert result.tool_calls[0]["args"] == {"query": "Jean Dupont"}
    (report,) = context.reports()
    assert (report.kind, report.outcome) == (CallKind.AGENT_STEP, CallOutcome.COMPLETED)
    assert report.tokens_restored == 2


def _failing_groq_sdk(monkeypatch, error):
    """The real ChatGroq, whose Groq SDK clients (sync and async) raise ``error``."""

    def create(**params):
        raise error

    async def acreate(**params):
        raise error

    client = llm_adapters._groq()
    monkeypatch.setattr(client, "client", type("SyncSdk", (), {"create": staticmethod(create)})())
    monkeypatch.setattr(
        client, "async_client", type("AsyncSdk", (), {"create": staticmethod(acreate)})()
    )


async def _collect(chunks):
    return [chunk async for chunk in chunks]


# Every public path that reaches the provider: LangChain sync/async/streaming, LlamaIndex.
PROVIDER_PATHS = {
    "invoke": lambda: GatewayChatModel(kind=CallKind.QUERY_REWRITE).invoke("q"),
    "ainvoke": lambda: asyncio.run(GatewayChatModel(kind=CallKind.AGENT_STEP).ainvoke("q")),
    "stream": lambda: list(GatewayChatModel(kind=CallKind.SELF_QUERY).stream("q")),
    "astream": lambda: asyncio.run(
        _collect(GatewayChatModel(kind=CallKind.AGENT_STEP).astream("q"))
    ),
    "complete": lambda: GatewayLlamaLLM().complete("q"),
    "acomplete": lambda: asyncio.run(GatewayLlamaLLM().acomplete("q")),
    "stream_complete": lambda: list(GatewayLlamaLLM().stream_complete("q")),
}


@pytest.mark.parametrize("path", PROVIDER_PATHS)
def test_a_groq_sdk_timeout_is_audited_as_a_timeout(groq_consent, monkeypatch, path):
    import httpx
    from groq import APITimeoutError

    request = httpx.Request("POST", "https://api.groq.com", content=b"PROTECTED-PROMPT")
    _failing_groq_sdk(monkeypatch, APITimeoutError(request=request))
    with _context() as context, pytest.raises(TimeoutError) as caught:
        PROVIDER_PATHS[path]()
    (report,) = context.reports()
    assert (report.outcome, report.error_type) == (CallOutcome.FAILED, "TimeoutError")
    error = caught.value
    assert type(error) is TimeoutError  # the built-in one, not the SDK's
    assert error.__cause__ is None and error.__context__ is None  # SDK error not chained
    assert "PROTECTED-PROMPT" not in str(error)


@pytest.mark.parametrize("path", ["invoke", "ainvoke", "complete"])
def test_other_groq_sdk_errors_stay_safe_component_errors(groq_consent, monkeypatch, path):
    import httpx
    from groq import APIConnectionError

    request = httpx.Request("POST", "https://api.groq.com")
    _failing_groq_sdk(monkeypatch, APIConnectionError(request=request))
    with _context() as context, pytest.raises(ProviderCallError) as caught:
        PROVIDER_PATHS[path]()
    (report,) = context.reports()
    assert (report.outcome, report.error_type) == (CallOutcome.FAILED, "ComponentError")
    assert caught.value.__cause__ is None and caught.value.__context__ is None


# --- retriever_tools: every LLM call of a strategy goes through the adapters ------------

STRATEGY_CALLS = {
    "vector_mmr": ([], []),  # no LLM at all
    "bm25": ([], []),
    "multi_query": ([CallKind.QUERY_REWRITE], ["procedure alpha\nreglement bravo"]),
    "self_query": ([CallKind.SELF_QUERY], [SELF_QUERY_REPLY]),
    "fusion": ([CallKind.QUERY_FUSION], ["procedure alpha\nreglement bravo\nprocedure"]),
}


@pytest.mark.parametrize("strategy", rt.STRATEGIES)
def test_each_retrieval_strategy_calls_the_llm_only_through_the_adapters(
    rag, provider, monkeypatch, strategy
):
    _, user = rag
    expected_kinds, replies = STRATEGY_CALLS[strategy]
    seen = _kinds_spy(monkeypatch)
    provider.replies = list(replies)
    with _with_known_context() as context:
        chunks = rt.retrieve(strategy, "procedure Jean Dupont", user, top_k=10)
    assert chunks and {c.business_layer for c in chunks} == {FRONT_OFFICE}  # scope kept
    assert [kind for _, kind in seen] == expected_kinds
    assert len(provider.calls) == len(expected_kinds)  # one provider call per report
    assert [r.kind for r in context.reports()] == expected_kinds
    assert all(r.outcome is CallOutcome.COMPLETED for r in context.reports())
    for call in provider.calls:  # protected before the provider
        assert "Jean Dupont" not in _sent_text(call)
    assert LlamaSettings._llm is None  # LlamaIndex never fell back to its default LLM


@pytest.mark.parametrize("strategy", ["multi_query", "self_query", "fusion"])
def test_an_llm_strategy_outside_a_request_context_fails_closed(rag, provider, strategy):
    _, user = rag
    provider.replies = ["never"]
    with pytest.raises(NoActiveRequest):
        rt.retrieve(strategy, "procedure", user)
    assert provider.calls == []


def _with_known_context():
    """A request context that pseudonymises the name used in the retrieval tests."""
    return LLMRequestContext(
        caller_id=42,
        limits=RequestLimits(max_provider_calls=5, max_prompt_chars=50_000, timeout_seconds=5.0),
        known_values={EntityType.PERSON: ["Jean Dupont"]},
    )


# --- RagService: isolation, interruption ---------------------------------------------


def test_an_interrupted_stream_closed_elsewhere_is_closed_and_audited(rag, provider, caplog):
    """A disconnected client: the stream is finalised later, from another Context."""
    import contextvars

    service, user = rag
    provider.replies = ["Réponse finale"]
    handed = []
    caplog.set_level(logging.ERROR)

    async def interrupt():
        events = service.stream(user, SECRET_QUESTION, audit=handed.append)
        first = [await anext(events), await anext(events)]
        await asyncio.create_task(events.aclose(), context=contextvars.Context())
        return first

    first = asyncio.run(interrupt())  # aclose() raises nothing
    assert [e["type"] for e in first] == ["session", "token"]
    (context,) = handed  # the audit hook still received the request
    assert context.closed and context._vault is None  # vault dropped
    assert [(r.kind, r.outcome) for r in context.reports()] == [
        (CallKind.AGENT_STEP, CallOutcome.COMPLETED)
    ]
    assert "Erreur agent" not in caplog.text  # no false agent error


def test_concurrent_requests_get_isolated_contexts(rag, provider, monkeypatch):
    service, _ = rag
    users = [rt.UserContext(user_id=uid, department=TEST_DEPARTMENT) for uid in (1, 2, 3)]
    seen = _kinds_spy(monkeypatch)
    provider.replies = ["a", "b", "c"]

    async def all_at_once():
        return await asyncio.gather(
            *(service.ask(user, "procedure", f"s{user.user_id}") for user in users)
        )

    asyncio.run(all_at_once())
    contexts = {id(context): context for context, _ in seen}
    assert len(contexts) == 3  # one context per request, never shared
    assert {c.caller_id for c in contexts.values()} == {1, 2, 3}
    for context in contexts.values():  # each request id only in its own context
        assert [r.request_id for r in context.reports()] == [context.request_id]
    assert {c["request_id"] for c in provider.calls} == {c.request_id for c in contexts.values()}
