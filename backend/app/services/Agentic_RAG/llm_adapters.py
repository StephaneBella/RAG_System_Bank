"""SEC-01 adapters: the only path from the RAG agent to the LLM provider (Groq).

The four provider calls of a RAG request go through here (``llm_request.CallKind``):

- ``AGENT_STEP``: the agent model (rag_agent.py), ``GatewayChatModel``;
- ``QUERY_REWRITE``: ``MultiQueryRetriever``, ``GatewayChatModel``;
- ``SELF_QUERY``: ``SecureSelfQueryRetriever``, ``GatewayChatModel``;
- ``QUERY_FUSION``: ``QueryFusionRetriever`` (LlamaIndex), ``GatewayLlamaLLM``.

Groq is an external provider: without ``settings.allow_external_llm`` (decision H1), the
client is never built, tools are never bound and nothing is sent (``ExternalProviderRefused``);
there is no other provider and no fallback.

Every call, whatever its kind:

0. ``allow_external_llm`` is checked first; a refused call is not counted;
1. ``require_request()``: the active ``LLMRequestContext`` of the request, opened by
   ``RagService.ask`` / ``stream``; without one, nothing is sent (fails closed);
2. ``context.call(kind)``: next ``call_index``, ``max_provider_calls`` enforced, one
   ``ProviderCallReport`` per call, without any text;
3. every text about to be sent is protected with ``context.protect`` (T3). The agent
   history stays in clear and is protected again at each call: one vault per request,
   so a value gets the same token in every call of the request;
4. ``call.sent(...)``: ``max_prompt_chars`` enforced, set of the tokens actually sent;
5. one request to Groq: identifier ``settings.llm_model`` (``groq:`` prefix removed),
   key ``settings.groq_api_key``, timeout ``context.limits.timeout_seconds``
   (``settings.timeout_seconds``), no retry: one call is one provider request;
6. ``call.restore(...)`` on the answer and on every tool-call argument: only the tokens
   sent by this call are restored; any other token stays literal.

Only the content, the tool calls and their identifiers leave the provider answer:
``additional_kwargs`` (reasoning included) are dropped. Nothing is logged here. A
provider error is replaced by ``ProviderCallError`` (a timeout by ``TimeoutError``),
without its message, and the local variables of the frames it went through are cleared.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import AsyncIterator, Iterator, Sequence
from functools import lru_cache
from typing import Any

from groq import APITimeoutError
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_groq import ChatGroq
from llama_index.core.llms import CompletionResponse, CompletionResponseGen, CustomLLM, LLMMetadata

from app.core.config import settings
from app.services.llm_request import (
    CallKind,
    CallLimitExceeded,
    LLMRequestContext,
    NoActiveRequest,
    PromptTooLong,
    ProviderCall,
    RequestClosed,
    require_request,
)

GROQ_PREFIX = "groq:"
_SCALARS = (int, float, bool, type(None))
# Raised unchanged (frames cleared): they carry no provider error and the caller maps them.
_PASSTHROUGH = (CallLimitExceeded, PromptTooLong, NoActiveRequest, RequestClosed)


class ProviderCallError(RuntimeError):
    """The provider call failed; the message never carries the original error."""

    def __init__(self, kind: CallKind):
        super().__init__(f"provider call failed ({kind.value})")


class ExternalProviderRefused(PermissionError):
    """Groq is an external provider: refused without allow_external_llm (decision H1)."""


# ---------------------------------------------------------------------------
# Provider client: Groq only, built from Settings
# ---------------------------------------------------------------------------
def _require_external_consent() -> None:
    """Checked before building, binding or invoking the provider, at every use."""
    if settings.allow_external_llm is not True:
        raise ExternalProviderRefused("the LLM provider (Groq) requires allow_external_llm")


def groq_model_id() -> str:
    """The raw Groq identifier expected by ChatGroq (``groq:`` is init_chat_model syntax)."""
    if not settings.llm_model:
        raise RuntimeError("llm_model is not configured")
    return settings.llm_model.removeprefix(GROQ_PREFIX)


@lru_cache(maxsize=1)
def _groq() -> ChatGroq:
    """The only constructor of the provider client."""
    _require_external_consent()
    return ChatGroq(
        model=groq_model_id(),
        api_key=settings.groq_api_key,
        temperature=0,
        max_retries=0,
    )


def _provider() -> BaseChatModel:
    """The provider client of every call; replaced by a fake provider in tests."""
    _require_external_consent()  # also when the client is already built (cached)
    return _groq()


# ---------------------------------------------------------------------------
# What is sent: protected copies, and the texts declared with call.sent()
# ---------------------------------------------------------------------------
class _Outgoing:
    def __init__(self, context: LLMRequestContext):
        self._context = context
        self.texts: list[str] = []

    def text(self, value: object) -> str:
        if type(value) is not str:
            raise TypeError(f"sent texts must be str, not {type(value).__name__}")
        protected = self._context.protect(value)
        self.texts.append(protected)
        return protected

    def args(self, value: object) -> object:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.args(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.args(item) for item in value]
        if isinstance(value, _SCALARS):
            return value
        raise TypeError(f"unsupported tool argument type {type(value).__name__}")

    def content(self, content: object) -> str | list:
        if isinstance(content, str):
            return self.text(content)
        if isinstance(content, list):
            blocks: list = []
            for block in content:
                if isinstance(block, str):
                    blocks.append(self.text(block))
                elif isinstance(block, dict) and block.get("type") == "text":
                    blocks.append({"type": "text", "text": self.text(block.get("text"))})
                else:
                    raise TypeError("unsupported message content block")
            return blocks
        raise TypeError(f"unsupported message content type {type(content).__name__}")

    def message(self, message: BaseMessage) -> BaseMessage:
        """A new message with only what the provider needs, every text protected."""
        if isinstance(message, SystemMessage):
            return SystemMessage(content=self.content(message.content))
        if isinstance(message, HumanMessage):
            return HumanMessage(content=self.content(message.content))
        if isinstance(message, ToolMessage):
            return ToolMessage(
                content=self.content(message.content),
                tool_call_id=message.tool_call_id,
                name=message.name,
            )
        if isinstance(message, AIMessage):
            return AIMessage(
                content=self.content(message.content),
                tool_calls=[
                    {
                        "name": call["name"],
                        "args": self.args(call["args"]),
                        "id": call.get("id"),
                        "type": "tool_call",
                    }
                    for call in message.tool_calls
                ],
                invalid_tool_calls=[
                    {
                        "name": call.get("name"),
                        "args": self.text(call["args"]) if call.get("args") else None,
                        "id": call.get("id"),
                        "error": None,
                        "type": "invalid_tool_call",
                    }
                    for call in message.invalid_tool_calls
                ],
            )
        raise TypeError(f"unsupported message type {type(message).__name__}")


def _declared(kwargs: dict[str, Any]) -> list[str]:
    """Tool schemas are sent too: constant texts, counted in max_prompt_chars."""
    tools = kwargs.get("tools")
    return [json.dumps(tools, sort_keys=True, ensure_ascii=False)] if tools else []


# ---------------------------------------------------------------------------
# What comes back: restored with the tokens sent by this call only
# ---------------------------------------------------------------------------
def _restored_args(call: ProviderCall, value: object) -> object:
    if isinstance(value, str):
        return call.restore(value)
    if isinstance(value, dict):
        return {key: _restored_args(call, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restored_args(call, item) for item in value]
    if isinstance(value, _SCALARS):
        return value
    raise TypeError(f"unsupported tool argument type {type(value).__name__}")


def _answer(call: ProviderCall, result: object) -> AIMessage:
    if not isinstance(result, AIMessage) or type(result.content) is not str:
        raise TypeError("the provider must answer with an AIMessage of str content")
    metadata = result.response_metadata
    return AIMessage(
        content=call.restore(result.content),
        tool_calls=[
            {
                "name": tool["name"],
                "args": _restored_args(call, tool["args"]),
                "id": tool.get("id"),
                "type": "tool_call",
            }
            for tool in result.tool_calls
        ],
        invalid_tool_calls=[
            {
                "name": tool.get("name"),
                "args": call.restore(tool["args"]) if tool.get("args") else None,
                "id": tool.get("id"),
                "error": None,
                "type": "invalid_tool_call",
            }
            for tool in result.invalid_tool_calls
        ],
        id=result.id,
        usage_metadata=result.usage_metadata,
        response_metadata={
            k: metadata[k] for k in ("model_name", "finish_reason") if k in metadata
        },
    )


# ---------------------------------------------------------------------------
# One provider call (sync / async)
# ---------------------------------------------------------------------------
# A Groq SDK timeout (groq.APITimeoutError) is not a built-in exception: the request
# context would report it as "ComponentError". It is turned into the built-in
# TimeoutError INSIDE the call, so that the report (and the audit) says TimeoutError.
# It is raised after the except block: the SDK error, which holds the HTTP request
# (the protected prompt), is not chained to it.
def _timed_out(kind: CallKind) -> TimeoutError:
    return TimeoutError(f"provider call timed out ({kind.value})")


def _sync_attempt(
    context: LLMRequestContext, kind: CallKind, messages: Sequence[BaseMessage], stop, kwargs
) -> AIMessage:
    with context.call(kind) as call:
        outgoing = _Outgoing(context)
        protected = [outgoing.message(message) for message in messages]
        call.sent(*outgoing.texts, *_declared(kwargs))
        timed_out = False
        try:
            result = _provider().invoke(
                protected, stop=stop, timeout=context.limits.timeout_seconds, **kwargs
            )
        except APITimeoutError:
            timed_out = True
        if timed_out:
            raise _timed_out(kind)
        return _answer(call, result)


async def _async_attempt(
    context: LLMRequestContext, kind: CallKind, messages: Sequence[BaseMessage], stop, kwargs
) -> AIMessage:
    timeout = context.limits.timeout_seconds
    with context.call(kind) as call:
        outgoing = _Outgoing(context)
        protected = [outgoing.message(message) for message in messages]
        call.sent(*outgoing.texts, *_declared(kwargs))
        timed_out = False
        try:
            result = await asyncio.wait_for(
                _provider().ainvoke(protected, stop=stop, timeout=timeout, **kwargs),
                timeout=timeout,
            )
        except APITimeoutError:
            timed_out = True
        if timed_out:
            raise _timed_out(kind)
        return _answer(call, result)


def _failure(error: BaseException, kind: CallKind) -> BaseException:
    """Clear the frames the error went through, then pick what the caller gets."""
    traceback.clear_frames(error.__traceback__)
    if isinstance(error, _PASSTHROUGH) or not isinstance(error, Exception):
        return error
    if isinstance(error, TimeoutError | APITimeoutError):
        return _timed_out(kind)
    return ProviderCallError(kind)


def chat(kind: CallKind, messages: Sequence[BaseMessage], stop=None, **kwargs) -> AIMessage:
    _require_external_consent()  # before the context: a refused call is never counted
    context = require_request()
    try:
        return _sync_attempt(context, kind, messages, stop, kwargs)
    except BaseException as error:
        failure = _failure(error, kind)
        if failure is error:
            raise
    raise failure  # outside the except block: the original error is not chained


async def achat(kind: CallKind, messages: Sequence[BaseMessage], stop=None, **kwargs) -> AIMessage:
    _require_external_consent()
    context = require_request()
    try:
        return await _async_attempt(context, kind, messages, stop, kwargs)
    except BaseException as error:
        failure = _failure(error, kind)
        if failure is error:
            raise
    raise failure


# ---------------------------------------------------------------------------
# LangChain adapter (langchain-core 1.7.2 BaseChatModel)
# ---------------------------------------------------------------------------
def _as_chunk(message: AIMessage) -> ChatGenerationChunk:
    """The whole restored answer as one chunk: nothing is streamed before restoration."""
    chunks = [
        tool_call_chunk(
            name=tool["name"],
            args=json.dumps(tool["args"], ensure_ascii=False),
            id=tool["id"],
            index=i,
        )
        for i, tool in enumerate(message.tool_calls)
    ] + [
        tool_call_chunk(name=tool["name"], args=tool["args"], id=tool["id"], index=i)
        for i, tool in enumerate(message.invalid_tool_calls, start=len(message.tool_calls))
    ]
    return ChatGenerationChunk(
        message=AIMessageChunk(
            content=message.content,
            id=message.id,
            tool_call_chunks=chunks,
            usage_metadata=message.usage_metadata,
            response_metadata=message.response_metadata,
        )
    )


class GatewayChatModel(BaseChatModel):
    """Chat model of the RAG agent and of the LangChain retrievers, bound to one kind."""

    kind: CallKind
    max_tokens: int = 1024

    @property
    def _llm_type(self) -> str:
        return "sec01-groq-gateway"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """Tools are formatted by ChatGroq.bind_tools itself (the provider client).

        Only the kwargs it produces (``tools``, ``tool_choice``…) are kept: the calls still
        go through ``chat`` / ``achat``, so through the request context. Building the
        client needs ``allow_external_llm``; binding sends nothing.
        """
        _require_external_consent()
        native = _groq().bind_tools(tools, tool_choice=tool_choice, **kwargs)
        return self.bind(**native.kwargs)

    def _params(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {"max_tokens": self.max_tokens, **kwargs}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = chat(self.kind, messages, stop, **self._params(kwargs))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = await achat(self.kind, messages, stop, **self._params(kwargs))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield _as_chunk(chat(self.kind, messages, stop, **self._params(kwargs)))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        yield _as_chunk(await achat(self.kind, messages, stop, **self._params(kwargs)))


# ---------------------------------------------------------------------------
# LlamaIndex adapter (llama-index-core 0.14.25 LLM, through CustomLLM)
# ---------------------------------------------------------------------------
class GatewayLlamaLLM(CustomLLM):
    """LLM of QueryFusionRetriever: Groq through the same SEC-01 path, kind QUERY_FUSION."""

    max_tokens: int = 1024

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            model_name="sec01-groq-gateway", is_chat_model=False, num_output=self.max_tokens
        )

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        answer = chat(
            CallKind.QUERY_FUSION, [HumanMessage(content=prompt)], max_tokens=self.max_tokens
        )
        return CompletionResponse(text=answer.content)

    async def acomplete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponse:
        answer = await achat(
            CallKind.QUERY_FUSION, [HumanMessage(content=prompt)], max_tokens=self.max_tokens
        )
        return CompletionResponse(text=answer.content)

    def stream_complete(
        self, prompt: str, formatted: bool = False, **kwargs: Any
    ) -> CompletionResponseGen:
        response = self.complete(prompt, formatted, **kwargs)

        def single() -> CompletionResponseGen:
            yield CompletionResponse(text=response.text, delta=response.text)

        return single()
