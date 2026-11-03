"""Masking and pseudonymisation of sensitive values (US 07 T3).

Standalone module: it is not wired into any prompt or route yet (US 07 T4/T5).
It applies the rules of docs/US-07-politique-minimisation.md:

- masking, irreversible (MIN-LLM-06): IBAN, payment cards, emails and phone
  numbers are replaced by a fixed label such as ``[IBAN]``;
- pseudonymisation, reversible (MIN-LLM-05, MIN-LLM-07): people, client
  identifiers and case numbers are replaced by tokens such as
  ``[PERSONNE_1_<vault id>]``, kept in an in-memory ``PseudonymVault``.

Scope decided for T3:

- no named-entity recognition: names are only pseudonymised when the caller
  supplies them, or through a detector plugged in later (``Detector``);
- client identifier and case number formats are configurable, with no
  pattern by default;
- phone numbers are only detected in international format (``+`` and code);
- an IBAN is masked whenever its format matches, even if its mod-97 key is
  invalid; a card number is masked only if its Luhn key is valid, so that
  amounts and references are left untouched.

Business content (amounts, thresholds, procedure references, dates) is never
altered. This module does not log anything, and neither ``Span`` nor
``PseudonymVault`` exposes a sensitive value in its ``repr``, ``str`` or errors.
"""

import re
import secrets
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class EntityType(str, Enum):
    IBAN = "IBAN"
    CARD = "CARTE"
    EMAIL = "EMAIL"
    PHONE = "TELEPHONE"
    PERSON = "PERSONNE"
    CLIENT_ID = "CLIENT"
    CASE_NUMBER = "DOSSIER"


MASKED_TYPES = frozenset({EntityType.IBAN, EntityType.CARD, EntityType.EMAIL, EntityType.PHONE})
PSEUDONYMISED_TYPES = frozenset({EntityType.PERSON, EntityType.CLIENT_ID, EntityType.CASE_NUMBER})


@dataclass(frozen=True)
class Span:
    """Position of a detected value. It deliberately holds no copy of the value."""

    start: int
    end: int
    entity_type: EntityType


class Detector(Protocol):
    """Extension point, e.g. for a named-entity recogniser chosen later."""

    def detect(self, text: str) -> Iterable[Span]: ...


@dataclass(frozen=True)
class RegexDetector:
    entity_type: EntityType
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = field(default=None, compare=False)

    def detect(self, text: str) -> Iterable[Span]:
        for match in self.pattern.finditer(text):
            if self.validator is None or self.validator(match.group()):
                yield Span(match.start(), match.end(), self.entity_type)


def _digits(value: str) -> str:
    return "".join(str(unicodedata.digit(ch)) for ch in value if ch.isdecimal())


def _luhn_ok(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


EMAIL_DETECTOR = RegexDetector(
    EntityType.EMAIL,
    re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
)


class _IbanDetector:
    """ISO 13616 format only: the mod-97 key is deliberately not checked.

    Trailing groups that hold no digit (a word such as ``TEST`` or ``EUR``) are not
    part of the IBAN, and groups beyond the 34-character maximum are left out.
    """

    entity_type = EntityType.IBAN
    _pattern = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
    _group = re.compile(r"[A-Z0-9]+")

    def detect(self, text: str) -> Iterator[Span]:
        for match in self._pattern.finditer(text):
            groups = list(self._group.finditer(text, match.start(), match.end()))
            while len(groups) > 1 and not any(ch.isdigit() for ch in groups[-1].group()):
                groups.pop()
            while len(groups) > 1 and sum(len(g.group()) for g in groups) > 34:
                groups.pop()
            if 15 <= sum(len(g.group()) for g in groups) <= 34:
                yield Span(match.start(), groups[-1].end(), self.entity_type)


class _CardDetector:
    """Finds a Luhn-valid card number, even inside a longer run of digits.

    A run such as ``2 4111 1111 1111 1111 12`` is split into its groups; the card is
    searched among consecutive groups shaped like a card (one block of 13 to 19
    digits, or the usual 4-4-4-4, 4-4-4-4-3, 4-4-4-3, 4-6-5 and 4-6-4 layouts).
    Amounts grouped by thousands therefore never match.
    """

    entity_type = EntityType.CARD
    _run = re.compile(r"(?<!\d)\d+(?:[ -]\d+)*(?!\d)")
    _group = re.compile(r"\d+")
    _shapes = frozenset({(4, 4, 4, 4), (4, 4, 4, 4, 3), (4, 4, 4, 3), (4, 6, 5), (4, 6, 4)})

    def _is_card_shape(self, shape: tuple[int, ...]) -> bool:
        return (len(shape) == 1 and 13 <= shape[0] <= 19) or shape in self._shapes

    def detect(self, text: str) -> Iterator[Span]:
        for run in self._run.finditer(text):
            groups = list(self._group.finditer(text, run.start(), run.end()))
            candidates = []
            for first in range(len(groups)):
                for last in range(first, min(first + 5, len(groups))):
                    window = groups[first : last + 1]
                    shape = tuple(len(g.group()) for g in window)
                    if self._is_card_shape(shape) and _luhn_ok("".join(g.group() for g in window)):
                        candidates.append((window[0].start(), window[-1].end()))
            taken: list[tuple[int, int]] = []
            # Longest card first, then leftmost; a card never overlaps another one.
            for start, end in sorted(candidates, key=lambda c: (c[0] - c[1], c[0])):
                if all(
                    end <= other_start or start >= other_end for other_start, other_end in taken
                ):
                    taken.append((start, end))
                    yield Span(start, end, self.entity_type)


class _PhoneDetector:
    """International numbers only (``+`` and a country code), 8 to 15 digits (E.164).

    Groups must share one separator (space, dot or hyphen), so a neighbouring number
    written differently is not absorbed; a ``(0)`` trunk prefix is accepted. A run
    followed by a currency, a percentage or a magnitude is an amount, not a phone.
    """

    entity_type = EntityType.PHONE
    _pattern = re.compile(r"(?<![\w+])\+[1-9]\d*(?: ?\(0\) ?\d+)?(?:([ .\-])\d+(?:\1\d+)*)?(?!\d)")
    _group = re.compile(r"\(0\)|\d+")
    _amount_unit = re.compile(
        r" ?(?:%|‰|€|\$|£|FCFA\b|F CFA\b|XAF\b|XOF\b|EUR\b|USD\b|M\b|Md\b|Mds\b|k\b"
        r"|millions?\b|milliards?\b|points?\b|pts?\b)",
        re.IGNORECASE,
    )

    def detect(self, text: str) -> Iterator[Span]:
        for match in self._pattern.finditer(text):
            if self._amount_unit.match(text, match.end()):
                continue
            end, count = None, 0
            for group in self._group.finditer(text, match.start(), match.end()):
                if group.group() == "(0)":
                    continue
                if count + len(group.group()) > 15:
                    break
                count += len(group.group())
                end = group.end()
            if end is not None and count >= 8:
                yield Span(match.start(), end, self.entity_type)


IBAN_DETECTOR = _IbanDetector()
CARD_DETECTOR = _CardDetector()
PHONE_DETECTOR = _PhoneDetector()

# Order matters: on spans of equal start and length, the first detector wins.
MASK_DETECTORS: tuple[Detector, ...] = (
    EMAIL_DETECTOR,
    IBAN_DETECTOR,
    CARD_DETECTOR,
    PHONE_DETECTOR,
)


def _detection_text(text: str) -> str:
    """Same-length copy where every Unicode space or format character is a plain space.

    Non-breaking or zero-width characters inside a number then cannot hide it, and
    spans stay valid on the original text, which is never normalised.
    """
    return "".join(
        " " if unicodedata.category(ch) in {"Zs", "Cf"} or ch == "\t" else ch for ch in text
    )


def _resolve(spans: Iterable[Span], text_length: int) -> list[Span]:
    """Keep non-overlapping spans: earliest first, then longest, then detector order."""
    ordered = []
    for rank, span in enumerate(spans):
        if not 0 <= span.start < span.end <= text_length:
            raise ValueError("Detector returned a span outside the text")
        ordered.append((span.start, span.start - span.end, rank, span))
    kept: list[Span] = []
    last_end = 0
    for _, _, _, span in sorted(ordered, key=lambda item: item[:3]):
        if span.start >= last_end:
            kept.append(span)
            last_end = span.end
    return kept


def detect(text: str, detectors: Sequence[Detector] = MASK_DETECTORS) -> list[Span]:
    """Non-overlapping spans of sensitive values found in ``text``."""
    scan = _detection_text(text)
    return _resolve((span for d in detectors for span in d.detect(scan)), len(text))


def _replace(text: str, spans: Sequence[Span], label: Callable[[Span], str]) -> str:
    parts = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(label(span))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def mask_label(entity_type: EntityType) -> str:
    return f"[{entity_type.value}]"


def mask_text(text: str, detectors: Sequence[Detector] = MASK_DETECTORS) -> str:
    """Irreversibly mask the values found by ``detectors``."""
    return _replace(text, detect(text, detectors), lambda span: mask_label(span.entity_type))


def _strings(values: object, name: str) -> list[str]:
    """Return ``values`` as a list of str, or refuse it.

    A lone string would be read character by character and protect nothing, so it is
    refused like any other non-conforming type. Error messages only name types, never
    the values themselves.
    """
    if isinstance(values, str | bytes | bytearray | Mapping) or not isinstance(values, Iterable):
        raise TypeError(f"{name} must be a collection of str, not {type(values).__name__}")
    items = list(values)
    for item in items:
        if not isinstance(item, str):
            raise TypeError(f"{name} must only contain str, not {type(item).__name__}")
    return items


def _known_values(known_values: object) -> dict[EntityType, list[str]]:
    """Validate the whole mapping before anything is protected: never a partial pass."""
    if not isinstance(known_values, Mapping):
        raise TypeError(f"known_values must be a mapping, not {type(known_values).__name__}")
    validated: dict[EntityType, list[str]] = {}
    for entity_type, values in known_values.items():
        if not isinstance(entity_type, EntityType):
            raise TypeError(
                f"known_values keys must be EntityType, not {type(entity_type).__name__}"
            )
        validated[entity_type] = _strings(values, "known_values")
    return validated


def _detectors(detectors: object) -> list[Detector]:
    if isinstance(detectors, str | bytes | bytearray | Mapping) or not isinstance(
        detectors, Iterable
    ):
        raise TypeError(f"detectors must be a collection, not {type(detectors).__name__}")
    items = list(detectors)
    for item in items:
        if not callable(getattr(item, "detect", None)):
            raise TypeError(f"detectors must provide detect(), not {type(item).__name__}")
    return items


def _require_text(text: object) -> None:
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, not {type(text).__name__}")


def _pattern_detectors(entity_type: EntityType, patterns: object, name: str) -> list[Detector]:
    return [RegexDetector(entity_type, re.compile(p)) for p in _strings(patterns, name)]


class _KnownValuesDetector:
    """Finds values supplied by the caller, as whole words, case-sensitively."""

    def __init__(self, known_values: Mapping[EntityType, list[str]]):
        self._patterns = [
            (entity_type, re.compile(rf"(?<!\w){re.escape(value.strip())}(?!\w)"))
            for entity_type, values in known_values.items()
            for value in values
            if value.strip()
        ]

    def detect(self, text: str) -> Iterable[Span]:
        for entity_type, pattern in self._patterns:
            for match in pattern.finditer(text):
                yield Span(match.start(), match.end(), entity_type)


class _OwnTokenDetector:
    """Finds tokens of this vault already present in a text to protect.

    They are pseudonymised again like any value, so that ``restore`` gives back the
    literal text and never a value: a token cannot be smuggled in to be restored.
    """

    def __init__(self, pattern: re.Pattern[str]):
        self._pattern = pattern

    def detect(self, text: str) -> Iterable[Span]:
        for match in self._pattern.finditer(text):
            yield Span(match.start(), match.end(), EntityType(match.group(1)))


class PseudonymVault:
    """In-memory correspondence table, specific to one instance and never persisted.

    Use one vault per request or conversation of a single user: ``restore`` only
    puts back values that were pseudonymised by this vault, i.e. values taken from
    content that user was already allowed to send or read. A token is a counter
    followed by a random 128-bit identifier of the vault (``[PERSONNE_1_<id>]``): it
    is stable within the vault, never derived from the value, and a token of another
    vault or an invented one is never restored. How long a vault lives is left to
    the caller.
    """

    def __init__(
        self,
        *,
        client_id_patterns: Sequence[str] = (),
        case_number_patterns: Sequence[str] = (),
        detectors: Sequence[Detector] = (),
    ):
        client_ids = _pattern_detectors(
            EntityType.CLIENT_ID, client_id_patterns, "client_id_patterns"
        )
        case_numbers = _pattern_detectors(
            EntityType.CASE_NUMBER, case_number_patterns, "case_number_patterns"
        )
        plugged = _detectors(detectors)
        self._vault_id = secrets.token_hex(16)
        types = "|".join(t.value for t in PSEUDONYMISED_TYPES)
        self._token_pattern = re.compile(rf"\[({types})_\d+_{self._vault_id}\]")
        self._detectors: tuple[Detector, ...] = (
            _OwnTokenDetector(self._token_pattern),
            *MASK_DETECTORS,
            *client_ids,
            *case_numbers,
            *plugged,
        )
        self._token_by_value: dict[tuple[EntityType, str], str] = {}
        self._value_by_token: dict[str, str] = {}
        self._counters: dict[EntityType, int] = {}

    def __repr__(self) -> str:
        return f"PseudonymVault(entries={len(self._value_by_token)})"

    def __len__(self) -> int:
        return len(self._value_by_token)

    def _token(self, entity_type: EntityType, value: str) -> str:
        key = (entity_type, value)
        token = self._token_by_value.get(key)
        if token is None:
            self._counters[entity_type] = self._counters.get(entity_type, 0) + 1
            token = f"[{entity_type.value}_{self._counters[entity_type]}_{self._vault_id}]"
            self._token_by_value[key] = token
            self._value_by_token[token] = value
        return token

    def protect(
        self, text: str, known_values: Mapping[EntityType, Iterable[str]] | None = None
    ) -> str:
        """Mask and pseudonymise ``text`` in a single pass.

        ``known_values`` lists values the caller already knows to be sensitive,
        such as client names, by entity type. A non-conforming argument (a lone
        string, ``None`` in place of a collection, a non-str value, a key that is not
        an ``EntityType``) raises ``TypeError`` before anything is processed.
        """
        _require_text(text)
        detectors = list(self._detectors)
        if known_values is not None:
            detectors.append(_KnownValuesDetector(_known_values(known_values)))

        def label(span: Span) -> str:
            if span.entity_type in PSEUDONYMISED_TYPES:
                return self._token(span.entity_type, text[span.start : span.end])
            return mask_label(span.entity_type)

        return _replace(text, detect(text, detectors), label)

    def restore(self, text: str) -> str:
        """Replace the tokens issued by this vault; any other token is left as it is."""
        _require_text(text)
        return self._token_pattern.sub(
            lambda match: self._value_by_token.get(match.group(), match.group()), text
        )
