"""Shared context of one request to the LLM (US 07, preparation of the RAG agent).

The RAG agent calls an LLM several times per question (agent steps, query rewriting,
self-query, fusion). Every call of one HTTP request shares one ``LLMRequestContext``:

- a ``request_id`` drawn on the server (``uuid4``), never derived from the question,
  the user, the session id given by the client or the time;
- one ``PseudonymVault`` for this request only (decision D2): a token of another
  request is never restored;
- ``protect``: masking and pseudonymisation (T3) of a clear text, once, when it is
  about to be sent. A text that is already protected must not be protected again: T3
  would pseudonymise its own tokens a second time;
- one ``call(kind)`` per provider call. The call records the tokens of what is actually
  sent and restores, in its answer, only those tokens (``restore_sent_tokens``, T5):
  a token from another call, another request or the model's imagination stays literal;
- a bounded number of calls (``max_provider_calls``) and of characters per call
  (``max_prompt_chars``); a refused call is never sent;
- one ``ProviderCallReport`` per call, without any text, for the audit journal;
- ``close``: the vault and the known values are dropped; every later use fails.

The adapters of the agent must not keep a context, a vault or a user: they fetch the
active context with ``require_request()`` at the moment of each call, which fails
closed when no request is active. Entering the context (``with LLMRequestContext(...)``)
makes it active and closes it on exit. A thread does not inherit the active context:
the code that runs a tool in another thread re-activates it with ``bind_request``.

Like the gateway, this module trusts the code that runs in the same process: the vault
can be reached by introspection while the request is open.
"""

import math
import threading
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Self

from app.services.llm_gateway import _error_name
from app.services.pseudonymization import EntityType, PseudonymVault
from app.services.rag_context import _frozen_known_values, _positive_int
from app.services.token_restore import ANY_TOKEN, restore_sent_tokens


class CallKind(str, Enum):
    AGENT_STEP = "AGENT_STEP"
    QUERY_REWRITE = "QUERY_REWRITE"
    SELF_QUERY = "SELF_QUERY"
    QUERY_FUSION = "QUERY_FUSION"


class CallOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    CALL_LIMIT_EXCEEDED = "CALL_LIMIT_EXCEEDED"
    PROMPT_TOO_LONG = "PROMPT_TOO_LONG"
    FAILED = "FAILED"


class NoActiveRequest(RuntimeError):
    """No open LLM request context: the LLM must not be called."""


class RequestClosed(RuntimeError):
    """The context, or the call, is over: it can no longer be used."""


class CallLimitExceeded(RuntimeError):
    """The request already made its maximum number of provider calls."""


class PromptTooLong(RuntimeError):
    """What would be sent is longer than the limit; nothing is sent."""


@dataclass(frozen=True)
class RequestLimits:
    """Limits of one request.

    ``max_provider_calls`` and ``max_prompt_chars`` are enforced by the context.
    ``timeout_seconds`` is only validated and stored here, never enforced by the
    context: the adapter that calls the provider must apply it to that call.
    """

    max_provider_calls: int
    max_prompt_chars: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        _positive_int(self.max_provider_calls, "max_provider_calls")
        _positive_int(self.max_prompt_chars, "max_prompt_chars")
        if type(self.timeout_seconds) not in (int, float):
            raise TypeError(
                f"timeout_seconds must be a number, not {type(self.timeout_seconds).__name__}"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite number above 0")


@dataclass(frozen=True)
class ProviderCallReport:
    """What happened during one provider call, without any text (D7)."""

    request_id: str
    call_index: int
    kind: CallKind
    outcome: CallOutcome
    caller_id: int
    prompt_chars: int | None
    answer_chars: int | None
    tokens_sent: int
    tokens_restored: int
    tokens_withheld: int
    duration_ms: int
    error_type: str | None = None


_current: ContextVar["LLMRequestContext | None"] = ContextVar("llm_request", default=None)


def require_request() -> "LLMRequestContext":
    """The active, open request context; fails closed otherwise."""
    context = _current.get()
    if context is None:
        raise NoActiveRequest("no active LLM request context")
    if context.closed:
        raise RequestClosed("the LLM request context is closed")
    return context


@contextmanager
def bind_request(context: "LLMRequestContext") -> Iterator["LLMRequestContext"]:
    """Make ``context`` active in the current thread or task, without closing it."""
    if not isinstance(context, LLMRequestContext):
        raise TypeError(f"context must be an LLMRequestContext, not {type(context).__name__}")
    if context.closed:
        raise RequestClosed("the LLM request context is closed")
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


class ProviderCall:
    """One provider call of a request; created by ``LLMRequestContext.call`` only."""

    def __init__(self, context: "LLMRequestContext", index: int, kind: CallKind):
        self._context = context
        self.index = index
        self.kind = kind
        self._sent: frozenset[str] | None = None  # tokens only, never the text
        self._prompt_chars: int | None = None
        self._answer_chars: int | None = None
        self._restored = 0
        self._withheld = 0
        self._outcome: CallOutcome | None = None
        self._open = True

    def __repr__(self) -> str:
        return f"ProviderCall(index={self.index}, kind={self.kind.value})"

    def _require_open(self) -> None:
        if not self._open:
            raise RequestClosed("the provider call is over")
        if self._context.closed:
            raise RequestClosed("the LLM request context is closed")

    def sent(self, *texts: str) -> None:
        """Record what is about to be sent (already protected), once per call.

        Only the size and the set of tokens are kept. Over ``max_prompt_chars``, the
        call is refused: ``PromptTooLong`` is raised and the provider must not be called.
        """
        self._require_open()
        if self._sent is not None:
            raise RuntimeError("sent() is called once per provider call")
        for text in texts:
            if type(text) is not str:
                raise TypeError(f"sent texts must be str, not {type(text).__name__}")
        self._prompt_chars = sum(len(text) for text in texts)
        if self._prompt_chars > self._context.limits.max_prompt_chars:
            self._outcome = CallOutcome.PROMPT_TOO_LONG
            raise PromptTooLong("the prompt is longer than max_prompt_chars")
        self._sent = frozenset(token for text in texts for token in ANY_TOKEN.findall(text))

    def restore(self, answer: str) -> str:
        """Restore, in ``answer``, only the tokens sent by this call.

        May be called several times (content, then each tool argument); the answer
        size and the counters add up.
        """
        self._require_open()
        if self._sent is None:
            raise RuntimeError("restore() needs sent() first")
        if type(answer) is not str:
            raise TypeError(f"answer must be a str, not {type(answer).__name__}")
        text, restored, withheld = self._context._restore(answer, self._sent)
        self._answer_chars = (self._answer_chars or 0) + len(answer)
        self._restored += restored
        self._withheld += withheld
        return text


class LLMRequestContext:
    """Context of one request; see the module docstring."""

    def __init__(
        self,
        *,
        caller_id: int,
        limits: RequestLimits,
        known_values: Mapping[EntityType, Iterable[str]] | None = None,
    ):
        if type(caller_id) is not int:
            raise TypeError(f"caller_id must be an int, not {type(caller_id).__name__}")
        if not isinstance(limits, RequestLimits):
            raise TypeError(f"limits must be RequestLimits, not {type(limits).__name__}")
        vault = PseudonymVault()
        known = None if known_values is None else _frozen_known_values(known_values)
        if known is not None:
            vault.protect("", known)  # validates the known values; protects nothing
        self.request_id = uuid.uuid4().hex
        self.caller_id = caller_id
        self.limits = limits
        self._vault: PseudonymVault | None = vault
        self._known: object = known
        self._lock = threading.RLock()  # tools may run in parallel threads
        self._next_index = 0
        self._reports: list[ProviderCallReport] = []
        self._closed = False
        self._token = None

    def __repr__(self) -> str:
        return f"LLMRequestContext(calls={self._next_index}, closed={self._closed})"

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> PseudonymVault:
        vault = self._vault
        if self._closed or vault is None:
            raise RequestClosed("the LLM request context is closed")
        return vault

    def protect(self, text: str) -> str:
        """Mask and pseudonymise a clear text with the vault of this request."""
        with self._lock:
            return self._require_open().protect(text, self._known)

    def _restore(self, answer: str, sent: frozenset[str]) -> tuple[str, int, int]:
        with self._lock:
            vault = self._require_open()
            # restore_sent_tokens reads the sent tokens from a text: give it only them.
            return restore_sent_tokens(vault, answer, " ".join(sorted(sent)))

    def _report(self, call: ProviderCall, outcome: CallOutcome, started: float, error) -> None:
        self._reports.append(
            ProviderCallReport(
                request_id=self.request_id,
                call_index=call.index,
                kind=call.kind,
                outcome=outcome,
                caller_id=self.caller_id,
                prompt_chars=call._prompt_chars,
                answer_chars=call._answer_chars,
                tokens_sent=len(call._sent or ()),
                tokens_restored=call._restored,
                tokens_withheld=call._withheld,
                duration_ms=int((time.monotonic() - started) * 1000),
                error_type=None if error is None else _error_name(error),
            )
        )

    @contextmanager
    def call(self, kind: CallKind) -> Iterator[ProviderCall]:
        """One provider call: ``sent`` what is sent, then ``restore`` the answer.

        Each call gets the next ``call_index`` and leaves one report, whatever happens.
        Errors are never swallowed; the report only keeps their safe type name.
        """
        if not isinstance(kind, CallKind):
            raise TypeError(f"kind must be a CallKind, not {type(kind).__name__}")
        started = time.monotonic()
        with self._lock:
            self._require_open()
            call = ProviderCall(self, self._next_index, kind)
            self._next_index += 1
            if call.index >= self.limits.max_provider_calls:
                self._report(call, CallOutcome.CALL_LIMIT_EXCEEDED, started, None)
                call._open = False
                raise CallLimitExceeded("max_provider_calls reached for this request")
        try:
            yield call
            if call._sent is None:
                raise RuntimeError("a provider call ended without sent()")
        except BaseException as error:
            outcome = call._outcome or CallOutcome.FAILED
            failure = error if outcome is CallOutcome.FAILED else None
            with self._lock:
                self._report(call, outcome, started, failure)
            raise
        else:
            with self._lock:
                self._report(call, CallOutcome.COMPLETED, started, None)
        finally:
            call._open = False

    def reports(self) -> tuple[ProviderCallReport, ...]:
        """Reports of every call so far; still available once the context is closed."""
        with self._lock:
            return tuple(self._reports)

    def close(self) -> None:
        """Drop the vault and the known values; idempotent."""
        with self._lock:
            self._closed = True
            self._vault = None
            self._known = None

    def __enter__(self) -> Self:
        self._require_open()
        if self._token is not None:
            raise RuntimeError("the LLM request context is already entered")
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self.close()
        finally:
            token, self._token = self._token, None
            _current.reset(token)
