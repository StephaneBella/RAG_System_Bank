"""Minimisation of the RAG context before it is sent to an LLM (US 07 T4).

Standalone module: it is not wired into any retriever, prompt or route yet. It
applies the rules of docs/US-07-politique-minimisation.md to extracts returned by a
future retriever (I-2):

- layer check after retrieval (MIN-LLM-02): an extract without a business layer, or
  whose layer is not among the layers allowed by the caller, is discarded. Layers
  are opaque strings: their mapping to departments is still open (decision H10);
- excluded sources (MIN-DOC-04): the caller must list them explicitly, e.g. the C3
  note ``Prive__Direction_Generale_Strategie.pdf`` of the T1 mapping; they are
  discarded before anything else;
- strictly necessary extracts (MIN-LLM-03, MIN-LLM-04): duplicates are removed,
  extracts are ranked by score when the retriever gives one, and the caller sets
  the maximum number of extracts and the context budget (decision H6, no default).
  An extract is never truncated: if it does not fit, it is discarded whole and the
  next ones are still examined;
- closed list of metadata (MIN-META-01 to 04): only ``source`` and ``page`` are
  kept; ``doc_type`` and ``business_layer`` only with ``include_classification``;
  ``file_path``, ``chunk_index`` and point identifiers never. A ``source`` holding a
  path separator is refused;
- optional pseudonymisation with a T3 ``PseudonymVault``: the text, the ``source``
  and a textual ``page`` are protected; an integer ``page`` keeps its type.

Every argument is validated before any processing, on a single copy of each
extract's metadata and of the known values, so that they cannot change between the
checks and the result. Text fields must be exact ``str``. Error messages and
``repr`` only name arguments and types, never text or metadata values. The returned
counters hold no content (MIN-LLM-09).

Obligations left to the gateway (T5) and to the retriever adapter (I-2):

- restore only the tokens present in the prompt actually sent: with a vault, an
  extract discarded for size has already been pseudonymised, so its values are in
  the vault although they were never transmitted, and a token guessed by the model
  would bring them back. This also applies to a vault reused across calls, which
  holds the values of every earlier call;
- use one vault per request or conversation, call ``restore`` once per answer, and
  discard the vault after any error, since an error may leave it partly filled;
- convert retriever scores to ``int`` or ``float`` and metadata to plain ``str`` or
  ``int`` values before building ``RetrievedChunk`` objects;
- journal each discarded extract as an event (MIN-LLM-02): the counters returned
  here are not enough for that, and the SEC-01 audit schema has to evolve first.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from app.services.pseudonymization import EntityType, PseudonymVault

TRANSMITTED_METADATA = ("source", "page")
CLASSIFICATION_METADATA = ("doc_type", "business_layer")
_TEXT_METADATA = ("source", "business_layer", "doc_type")
_PATH_SEPARATORS = ("/", "\\")


class ExclusionReason(str, Enum):
    EMPTY_TEXT = "EMPTY_TEXT"
    MISSING_SOURCE = "MISSING_SOURCE"
    EXCLUDED_SOURCE = "EXCLUDED_SOURCE"
    MISSING_LAYER = "MISSING_LAYER"
    LAYER_NOT_ALLOWED = "LAYER_NOT_ALLOWED"
    DUPLICATE = "DUPLICATE"
    MAX_EXTRACTS = "MAX_EXTRACTS"
    CONTEXT_BUDGET = "CONTEXT_BUDGET"


@dataclass(frozen=True)
class RetrievedChunk:
    """An extract as a retriever returns it: text, payload metadata, optional score."""

    text: str = field(repr=False)
    metadata: Mapping[str, object] = field(repr=False)
    score: float | None = None


@dataclass(frozen=True)
class ContextExtract:
    """An extract allowed into the context: its text and the transmitted metadata."""

    text: str = field(repr=False)
    metadata: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True)
class ContextStats:
    """Counters without content: how many extracts were received, kept and why not.

    ``context_chars`` counts the characters of the transmitted texts and of the
    transmitted metadata values.
    """

    received: int
    retained: int
    excluded: Mapping[ExclusionReason, int]
    context_chars: int


@dataclass(frozen=True)
class MinimizedContext:
    extracts: tuple[ContextExtract, ...]
    stats: ContextStats


@dataclass(frozen=True)
class _Checked:
    """A validated extract, built from one copy of its metadata."""

    text: str
    metadata: dict[str, object]
    score: float | None


def _collection(values: object, name: str) -> list[object]:
    if isinstance(values, str | bytes | bytearray | Mapping) or not isinstance(values, Iterable):
        raise TypeError(f"{name} must be a collection, not {type(values).__name__}")
    return list(values)


def _labels(values: object, name: str) -> frozenset[str]:
    items = _collection(values, name)
    if not items:
        raise ValueError(f"{name} must not be empty")
    for item in items:
        if type(item) is not str:
            raise TypeError(f"{name} must only contain str, not {type(item).__name__}")
        if not item.strip():
            raise ValueError(f"{name} must not contain blank values")
    return frozenset(items)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _check_chunk(chunk: object) -> _Checked:
    if not isinstance(chunk, RetrievedChunk):
        raise TypeError(f"chunks must only contain RetrievedChunk, not {type(chunk).__name__}")
    if type(chunk.text) is not str:
        raise TypeError(f"chunk text must be a str, not {type(chunk.text).__name__}")
    if not isinstance(chunk.metadata, Mapping):
        raise TypeError(f"chunk metadata must be a mapping, not {type(chunk.metadata).__name__}")
    metadata = dict(chunk.metadata)  # the only read: every later step uses this copy
    for key in metadata:
        if type(key) is not str:
            raise TypeError(f"metadata keys must be str, not {type(key).__name__}")
    for key in _TEXT_METADATA:
        value = metadata.get(key)
        if value is not None and type(value) is not str:
            raise TypeError(f"metadata {key} must be a str, not {type(value).__name__}")
    source = metadata.get("source")
    if source is not None and any(sep in source for sep in _PATH_SEPARATORS):
        raise ValueError("metadata source must not contain a path separator")
    page = metadata.get("page")
    if page is not None and type(page) not in (int, str):
        raise TypeError(f"metadata page must be an int or a str, not {type(page).__name__}")
    score = chunk.score
    if score is not None:
        if type(score) not in (int, float):
            raise TypeError(f"chunk score must be a number, not {type(score).__name__}")
        if not math.isfinite(score):
            raise ValueError("chunk score must be finite")
    return _Checked(chunk.text, metadata, score)


def _check_chunks(chunks: object) -> list[_Checked]:
    checked = [_check_chunk(chunk) for chunk in _collection(chunks, "chunks")]
    with_score = sum(item.score is not None for item in checked)
    if 0 < with_score < len(checked):
        raise ValueError("chunk scores must be given for every chunk or for none")
    return checked


def _frozen_known_values(known_values: object) -> object:
    """Read the known values once, as tuples, so that every call sees the same values.

    A lone string, a mapping or a non-iterable is kept as it is: T3 refuses it.
    """
    if not isinstance(known_values, Mapping):
        return known_values
    frozen: dict[object, object] = {}
    for entity_type, values in dict(known_values).items():
        if isinstance(values, str | bytes | bytearray | Mapping) or not isinstance(
            values, Iterable
        ):
            frozen[entity_type] = values
        else:
            frozen[entity_type] = tuple(values)
    return frozen


def _scope_reason(
    item: _Checked, allowed_layers: frozenset[str], excluded_sources: frozenset[str]
) -> ExclusionReason | None:
    """Why an extract is out of scope, checked before any context is built."""
    if not item.text.strip():
        return ExclusionReason.EMPTY_TEXT
    source = item.metadata.get("source")
    if source is None or not source.strip():
        return ExclusionReason.MISSING_SOURCE
    if source in excluded_sources:
        return ExclusionReason.EXCLUDED_SOURCE
    layer = item.metadata.get("business_layer")
    if layer is None or not layer.strip():
        return ExclusionReason.MISSING_LAYER
    if layer not in allowed_layers:
        return ExclusionReason.LAYER_NOT_ALLOWED
    return None


def _transmitted_metadata(
    metadata: dict[str, object], include_classification: bool
) -> dict[str, object]:
    keys = TRANSMITTED_METADATA + (CLASSIFICATION_METADATA if include_classification else ())
    return {key: metadata[key] for key in keys if metadata.get(key) is not None}


def _size(text: str, metadata: Mapping[str, object]) -> int:
    return len(text) + sum(len(str(value)) for value in metadata.values())


def build_context(
    chunks: Iterable[RetrievedChunk],
    *,
    allowed_layers: Iterable[str],
    excluded_sources: Iterable[str],
    max_extracts: int,
    max_context_chars: int,
    include_classification: bool = False,
    vault: PseudonymVault | None = None,
    known_values: Mapping[EntityType, Iterable[str]] | None = None,
) -> MinimizedContext:
    """Keep only the extracts that may be sent to an LLM, with minimal metadata.

    The context size counts the transmitted texts and metadata values, measured after
    pseudonymisation since that is what would be transmitted. ``known_values`` is
    only accepted with a ``vault``. See the module docstring for what T5 must do.
    """
    checked = _check_chunks(chunks)
    layers = _labels(allowed_layers, "allowed_layers")
    excluded = _labels(excluded_sources, "excluded_sources")
    extracts_limit = _positive_int(max_extracts, "max_extracts")
    chars_limit = _positive_int(max_context_chars, "max_context_chars")
    if type(include_classification) is not bool:
        raise TypeError(
            f"include_classification must be a bool, not {type(include_classification).__name__}"
        )
    if vault is not None and not isinstance(vault, PseudonymVault):
        raise TypeError(f"vault must be a PseudonymVault, not {type(vault).__name__}")
    known = None
    if known_values is not None:
        if vault is None:
            raise ValueError("known_values requires a vault")
        known = _frozen_known_values(known_values)
        vault.protect("", known)  # validates the frozen values; protects nothing yet

    def protect(value: str) -> str:
        return value if vault is None else vault.protect(value, known)

    excluded_counts: dict[ExclusionReason, int] = {}

    def exclude(reason: ExclusionReason) -> None:
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    candidates = []
    for item in checked:
        reason = _scope_reason(item, layers, excluded)
        if reason is None:
            candidates.append(item)
        else:
            exclude(reason)

    if candidates and candidates[0].score is not None:
        candidates.sort(key=lambda item: -item.score)  # stable: ties keep retriever order

    extracts: list[ContextExtract] = []
    seen: set[str] = set()
    used_chars = 0
    for item in candidates:
        key = " ".join(item.text.split())
        if key in seen:
            exclude(ExclusionReason.DUPLICATE)
            continue
        seen.add(key)
        if len(extracts) >= extracts_limit:
            exclude(ExclusionReason.MAX_EXTRACTS)
            continue
        text = protect(item.text)
        metadata = _transmitted_metadata(item.metadata, include_classification)
        for name in TRANSMITTED_METADATA:
            if type(metadata.get(name)) is str:
                metadata[name] = protect(metadata[name])
        size = _size(text, metadata)
        if used_chars + size > chars_limit:
            exclude(ExclusionReason.CONTEXT_BUDGET)
            continue
        used_chars += size
        extracts.append(ContextExtract(text, MappingProxyType(metadata)))

    stats = ContextStats(
        received=len(checked),
        retained=len(extracts),
        excluded=MappingProxyType(excluded_counts),
        context_chars=used_chars,
    )
    return MinimizedContext(tuple(extracts), stats)
