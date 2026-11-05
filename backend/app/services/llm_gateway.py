"""Internal LLM gateway (US 07 T5): the only path from a question to an LLM provider.

Standalone service: no HTTP route, no real LLM client and no retriever are defined
here (I-2 to I-4). The caller supplies them through ``Retriever``, ``PromptAssembler``
and ``LLMProvider``, together with the allowed layers and the excluded sources, which
it computes itself (decisions H10 and H11 stay outside the gateway).

One call to ``ask`` runs, in this order:

1. every argument is validated, and the known values are read once;
2. a new ``PseudonymVault`` is created for this request only (decision D2);
3. the question is masked and pseudonymised (T3) before the retriever and the
   assembler see it;
4. the retrieved extracts go through ``build_context`` (T4): layer check, excluded
   sources, duplicates, limits, minimal metadata, pseudonymisation;
5. without any authorised extract, the provider is not called (D6);
6. the assembled prompt is refused, not truncated, when it is longer than
   ``max_prompt_chars``; the provider is then not called (D4);
7. the provider receives the prompt and the caller's timeout (D4);
8. the answer is restored in a single ``restore`` call, and only the tokens present
   in the prompt actually sent are restored: any other token, from an extract
   discarded for size, another request or the model's imagination, stays literal.

After the call, whether it returns, fails or is interrupted, the gateway clears its
own references: the vault, the raw question, the known values, the raw extracts and
the restored answer are kept in an internal container that is emptied in a
``finally`` block, so the gateway frames hold none of them once ``ask`` is over, even
if a component keeps one of these frames or an error that went through them. The
caller's own frames still hold what the caller passed.

Errors (``Exception``) raised by the retriever, the assembler or the provider are
replaced by a ``GatewayError`` that only names the failed stage and a safe error type
(the name of a built-in exception, otherwise ``ComponentError``), without their
message, traceback or context. Other ``BaseException`` (``KeyboardInterrupt``,
``SystemExit``...) are re-raised unchanged; only the local variables of the frames
they went through are cleared.

To clear those references, ``traceback.clear_frames`` empties the local variables of
every frame the error went through, the components' own frames included: a
component that keeps its exception, or an inner one, loses the local variables it
could have inspected for diagnosis, and an ``Exception`` passed to the gateway also
loses its traceback. The caller only gets the stage and the error type.

Components run in the same process as the gateway and are trusted code: while they
run they can reach any object, the vault included, by introspection. Objects they
supply and whose methods run inside T4 (a custom mapping as metadata, for example)
execute in T4 frames, which the gateway cannot clear.

The report returned with each call holds no text: an opaque caller id, the T4
counters, sizes, the number of tokens actually restored and withheld, the duration
and the outcome (D7). The question length is not reported (decision H8 is open).
Writing it to the audit journal, one event per discarded extract included, needs the
SEC-01 schema to evolve first. Conversation history (D3), rate limiting (D5) and
prompt-injection detection (D1) are not handled here.
"""

import builtins
import math
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from app.services.pseudonymization import EntityType, PseudonymVault
from app.services.rag_context import (
    ContextExtract,
    ContextStats,
    RetrievedChunk,
    _frozen_known_values,
    build_context,
)
from app.services.token_restore import restore_sent_tokens


class Retriever(Protocol):
    def __call__(self, question: str) -> Iterable[RetrievedChunk]: ...


class PromptAssembler(Protocol):
    def __call__(self, question: str, extracts: Sequence[ContextExtract]) -> str: ...


class LLMProvider(Protocol):
    def complete(self, prompt: str, *, timeout_seconds: float) -> str: ...


class Outcome(str, Enum):
    ANSWERED = "ANSWERED"
    NO_AUTHORIZED_CONTEXT = "NO_AUTHORIZED_CONTEXT"
    PROMPT_TOO_LONG = "PROMPT_TOO_LONG"
    FAILED = "FAILED"


class Stage(str, Enum):
    QUESTION = "QUESTION"
    RETRIEVAL = "RETRIEVAL"
    CONTEXT = "CONTEXT"
    ASSEMBLY = "ASSEMBLY"
    PROVIDER = "PROVIDER"
    RESTORE = "RESTORE"


@dataclass(frozen=True)
class GatewayReport:
    """What happened during one call, without any text (D7)."""

    outcome: Outcome
    caller_id: int | None
    context: ContextStats | None
    prompt_chars: int | None
    answer_chars: int | None
    tokens_restored: int
    tokens_withheld: int
    duration_ms: int
    failed_stage: Stage | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class GatewayResult:
    answer: str | None = field(repr=False)
    report: GatewayReport


class GatewayError(Exception):
    """A stage of the gateway failed; the message never carries the original error."""

    def __init__(self, report: GatewayReport):
        super().__init__(f"LLM gateway failed at stage {report.failed_stage.value}")
        self.report = report


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _validate(
    question: object,
    max_prompt_chars: object,
    timeout_seconds: object,
    retriever: object,
    assembler: object,
    provider: object,
    caller_id: object,
) -> None:
    if type(question) is not str:
        raise TypeError(f"question must be a str, not {type(question).__name__}")
    if not question.strip():
        raise ValueError("question must not be blank")
    _positive_int(max_prompt_chars, "max_prompt_chars")
    if type(timeout_seconds) not in (int, float):
        raise TypeError(f"timeout_seconds must be a number, not {type(timeout_seconds).__name__}")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a finite number above 0")
    if not callable(retriever):
        raise TypeError(f"retriever must be callable, not {type(retriever).__name__}")
    if not callable(assembler):
        raise TypeError(f"assembler must be callable, not {type(assembler).__name__}")
    if not callable(getattr(provider, "complete", None)):
        raise TypeError(f"provider must provide complete(), not {type(provider).__name__}")
    if caller_id is not None and type(caller_id) is not int:
        raise TypeError(f"caller_id must be an int, not {type(caller_id).__name__}")


def _error_name(error: BaseException) -> str:
    """A safe, bounded error type: a built-in exception name, never a custom one."""
    kind = type(error)
    return kind.__name__ if getattr(builtins, kind.__name__, None) is kind else "ComponentError"


def _materialised(chunks: object) -> object:
    """Read an iterable once: the retriever output during the retrieval stage (V6), and
    the allowed layers and excluded sources before T4 validates them.

    A lone string, a mapping or a non-iterable is passed on as it is: T4 refuses it.
    """
    if isinstance(chunks, str | bytes | bytearray | Mapping) or not isinstance(chunks, Iterable):
        return chunks
    return tuple(chunks)


@dataclass
class _Progress:
    stage: Stage = Stage.QUESTION
    context: ContextStats | None = None
    prompt_chars: int | None = None
    answer_chars: int | None = None


class _Sensitive:
    """Holds every sensitive reference of one call; emptied when the call ends."""

    def __init__(self, vault: PseudonymVault, question: str, known: object):
        self.vault: PseudonymVault | None = vault
        self.question: str | None = question
        self.known: object = known
        self.chunks: object = None
        self.answer: str | None = None

    def __repr__(self) -> str:
        return "_Sensitive()"

    def clear(self) -> None:
        self.vault = self.question = self.known = self.chunks = self.answer = None


def _run(
    sensitive: _Sensitive,
    progress: _Progress,
    retriever: Retriever,
    assembler: PromptAssembler,
    provider: LLMProvider,
    context_options: dict[str, object],
    max_prompt_chars: int,
    timeout_seconds: float,
) -> tuple[Outcome, int, int]:
    """Run the stages; sensitive values only live in ``sensitive``, never in locals."""
    progress.stage = Stage.QUESTION
    protected_question = sensitive.vault.protect(sensitive.question, sensitive.known)
    sensitive.question = None

    progress.stage = Stage.RETRIEVAL
    sensitive.chunks = _materialised(retriever(protected_question))

    progress.stage = Stage.CONTEXT
    context = build_context(
        sensitive.chunks, vault=sensitive.vault, known_values=sensitive.known, **context_options
    )
    sensitive.chunks = None
    progress.context = context.stats
    if not context.extracts:
        return Outcome.NO_AUTHORIZED_CONTEXT, 0, 0

    progress.stage = Stage.ASSEMBLY
    prompt = assembler(protected_question, context.extracts)
    if type(prompt) is not str:
        raise TypeError("assembler must return a str")
    progress.prompt_chars = len(prompt)
    if len(prompt) > max_prompt_chars:
        return Outcome.PROMPT_TOO_LONG, 0, 0

    progress.stage = Stage.PROVIDER
    answer = provider.complete(prompt, timeout_seconds=timeout_seconds)
    if type(answer) is not str:
        raise TypeError("provider must return a str")
    progress.answer_chars = len(answer)

    progress.stage = Stage.RESTORE
    sensitive.answer, restored, withheld = restore_sent_tokens(sensitive.vault, answer, prompt)
    return Outcome.ANSWERED, restored, withheld


def ask(
    question: str,
    *,
    allowed_layers: Iterable[str],
    excluded_sources: Iterable[str],
    max_extracts: int,
    max_context_chars: int,
    max_prompt_chars: int,
    timeout_seconds: float,
    retriever: Retriever,
    assembler: PromptAssembler,
    provider: LLMProvider,
    known_values: Mapping[EntityType, Iterable[str]] | None = None,
    include_classification: bool = False,
    caller_id: int | None = None,
) -> GatewayResult:
    """Answer one question through the gateway; see the module docstring."""
    _validate(
        question, max_prompt_chars, timeout_seconds, retriever, assembler, provider, caller_id
    )
    known = None if known_values is None else _frozen_known_values(known_values)
    # Read once: the validation below and the real call must see the same scopes.
    context_options = {
        "allowed_layers": _materialised(allowed_layers),
        "excluded_sources": _materialised(excluded_sources),
        "max_extracts": max_extracts,
        "max_context_chars": max_context_chars,
        "include_classification": include_classification,
    }
    # Validates the T4 options and the known values without protecting anything.
    build_context([], vault=PseudonymVault(), known_values=known, **context_options)

    sensitive = _Sensitive(PseudonymVault(), question, known)
    question = known_values = known = None  # from now on, only in the container
    started = time.monotonic()
    progress = _Progress()
    error_type = None

    def report(outcome: Outcome, restored: int = 0, withheld: int = 0) -> GatewayReport:
        return GatewayReport(
            outcome=outcome,
            caller_id=caller_id,
            context=progress.context,
            prompt_chars=progress.prompt_chars,
            answer_chars=progress.answer_chars,
            tokens_restored=restored,
            tokens_withheld=withheld,
            duration_ms=int((time.monotonic() - started) * 1000),
            failed_stage=progress.stage if error_type is not None else None,
            error_type=error_type,
        )

    try:
        outcome, restored, withheld = _run(
            sensitive,
            progress,
            retriever,
            assembler,
            provider,
            context_options,
            max_prompt_chars,
            timeout_seconds,
        )
        # The answer leaves the container in the returned object only, never in a local.
        return GatewayResult(sensitive.answer, report(outcome, restored, withheld))
    except BaseException as error:  # gateway boundary, see the module docstring
        # The error may outlive this call: clear the local variables of the frames it
        # went through, which may hold sensitive values.
        traceback.clear_frames(error.__traceback__)
        if not isinstance(error, Exception):
            raise  # KeyboardInterrupt, SystemExit...: never intercepted
        error_type = _error_name(error)
        error.__traceback__ = None
    finally:
        sensitive.clear()  # after a success, an error or an interruption alike
    # Raised outside the except block: the original error is not chained.
    raise GatewayError(report(Outcome.FAILED))
