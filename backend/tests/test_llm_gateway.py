# US 07 T5 / SEC-02 T5 : passerelle LLM interne, testée sans route, sans client réel.
#
# Le faux récupérateur renvoie des extraits synthétiques portant un marqueur unique par
# couche ; le faux fournisseur capture le prompt réellement transmis. Le cloisonnement
# est vérifié sur ce prompt capturé.

import gc
import inspect
import math
import re
import sys
import weakref

import pytest

from app.services import llm_gateway
from app.services.llm_gateway import (
    GatewayError,
    GatewayResult,
    Outcome,
    Stage,
    ask,
)
from app.services.pseudonymization import EntityType, PseudonymVault
from app.services.rag_context import ExclusionReason, RetrievedChunk

C3_SOURCE = "Prive__Direction_Generale_Strategie.pdf"
LAYER = "layer_a"
OTHER_LAYER = "layer_b"
NAME = "Awa Ndiaye"
EMAIL = "awa.test@example.org"
IBAN = "FR76 3000 6000 0112 3456 7890 189"
TOKEN = re.compile(r"\[(PERSONNE|CLIENT|DOSSIER)_(\d+)_([0-9a-f]{32})\]")
DEFAULT_CHUNKS = (RetrievedChunk("Procédure A.", {"source": "doc_a.pdf", "business_layer": LAYER}),)


def chunk(text, *, source="doc_a.pdf", layer=LAYER, score=None, **meta):
    metadata = {"source": source, "business_layer": layer, **meta}
    return RetrievedChunk(text, {k: v for k, v in metadata.items() if v is not None}, score)


class FakeRetriever:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.questions = []

    def __call__(self, question):
        self.questions.append(question)
        return list(self.chunks)


class FakeAssembler:
    def __init__(self):
        self.calls = []

    def __call__(self, question, extracts):
        self.calls.append((question, extracts))
        lines = [f"Question : {question}"]
        lines += [f"[{e.metadata['source']}] {e.text}" for e in extracts]
        return "\n".join(lines)


class FakeProvider:
    """Captures every prompt and timeout; answers with ``reply(prompt)``."""

    def __init__(self, reply=lambda prompt: "Réponse.", error=None):
        self.reply = reply
        self.error = error
        self.prompts = []
        self.timeouts = []

    def complete(self, prompt, *, timeout_seconds):
        self.prompts.append(prompt)
        self.timeouts.append(timeout_seconds)
        if self.error is not None:
            raise self.error
        return self.reply(prompt)


def gateway(question="Quelle est la procédure ?", chunks=DEFAULT_CHUNKS, **overrides):
    retriever = overrides.pop("retriever", FakeRetriever(chunks))
    assembler = overrides.pop("assembler", FakeAssembler())
    provider = overrides.pop("provider", FakeProvider())
    options = {
        "allowed_layers": [LAYER],
        "excluded_sources": [C3_SOURCE],
        "max_extracts": 10,
        "max_context_chars": 10_000,
        "max_prompt_chars": 10_000,
        "timeout_seconds": 30,
        "retriever": retriever,
        "assembler": assembler,
        "provider": provider,
    }
    options.update(overrides)
    return options, retriever, assembler, provider


def run(question="Quelle est la procédure ?", chunks=DEFAULT_CHUNKS, **overrides):
    options, retriever, assembler, provider = gateway(question, chunks, **overrides)
    return ask(question, **options), retriever, assembler, provider


# --- Cas nominal -------------------------------------------------------------------------


def test_a_question_is_answered_through_the_gateway():
    result, retriever, assembler, provider = run(caller_id=7)
    assert isinstance(result, GatewayResult)
    assert result.answer == "Réponse."
    assert len(retriever.questions) == len(assembler.calls) == len(provider.prompts) == 1
    report = result.report
    assert report.outcome is Outcome.ANSWERED
    assert report.caller_id == 7
    assert report.context.retained == 1
    assert report.prompt_chars == len(provider.prompts[0])
    assert report.answer_chars == len("Réponse.")
    assert (report.failed_stage, report.error_type) == (None, None)
    assert report.duration_ms >= 0


@pytest.mark.parametrize("timeout", [30, 2.5])
def test_the_timeout_is_passed_to_the_provider(timeout):
    _, _, _, provider = run(timeout_seconds=timeout)
    assert provider.timeouts == [timeout]


# --- Cloisonnement : contenu du prompt réellement transmis (SEC-02 T5) -------------------


def test_only_authorised_layers_reach_the_captured_prompt():
    chunks = [
        chunk("MARQUEUR_A procédure autorisée.", score=0.1),
        chunk("MARQUEUR_B autre couche.", layer=OTHER_LAYER, score=0.9),
        chunk("MARQUEUR_C3 note stratégique.", source=C3_SOURCE, score=0.8),
        chunk("MARQUEUR_SANS_COUCHE.", layer=None, score=0.7),
        chunk("MARQUEUR_SANS_SOURCE.", source=None, score=0.6),
    ]
    result, _, assembler, provider = run(chunks=chunks)
    [prompt] = provider.prompts
    assert "MARQUEUR_A" in prompt
    for marker in ("MARQUEUR_B", "MARQUEUR_C3", "MARQUEUR_SANS_COUCHE", "MARQUEUR_SANS_SOURCE"):
        assert marker not in prompt
    assert C3_SOURCE not in prompt
    [(_, extracts)] = assembler.calls
    assert [e.text for e in extracts] == ["MARQUEUR_A procédure autorisée."]
    assert result.report.context.excluded  # counted, without content


def test_internal_metadata_never_reach_the_assembler():
    chunks = [chunk("Texte.", page=2, file_path="/srv/x.pdf", chunk_index=3, doc_type="prive")]
    _, _, assembler, provider = run(chunks=chunks)
    [(_, extracts)] = assembler.calls
    assert dict(extracts[0].metadata) == {"source": "doc_a.pdf", "page": 2}
    assert "/srv" not in provider.prompts[0]


def test_classification_is_passed_only_on_request():
    chunks = [chunk("Texte.", doc_type="prive")]
    _, _, assembler, _ = run(chunks=chunks, include_classification=True)
    [(_, extracts)] = assembler.calls
    assert extracts[0].metadata["business_layer"] == LAYER


def test_each_request_applies_its_own_layer_filter():
    chunks = [chunk("MARQUEUR_A."), chunk("MARQUEUR_B.", layer=OTHER_LAYER)]
    _, _, _, first = run(chunks=chunks, allowed_layers=[LAYER])
    _, _, _, second = run(chunks=chunks, allowed_layers=[OTHER_LAYER])
    assert "MARQUEUR_B" not in first.prompts[0] and "MARQUEUR_A" in first.prompts[0]
    assert "MARQUEUR_A" not in second.prompts[0] and "MARQUEUR_B" in second.prompts[0]


# --- Question protégée avant le récupérateur et l'assembleur -----------------------------


def test_the_question_is_protected_before_retrieval_and_assembly():
    question = f"Le dossier de {NAME} ({EMAIL}, {IBAN}) est-il complet ?"
    result, retriever, assembler, provider = run(
        question,
        known_values={EntityType.PERSON: [NAME]},
        provider=FakeProvider(lambda prompt: "Oui pour " + TOKEN.search(prompt).group() + "."),
    )
    [seen] = retriever.questions
    assert assembler.calls[0][0] == seen
    for value in (NAME, EMAIL, "FR76", "3000"):
        assert value not in seen
        assert value not in provider.prompts[0]
    assert "[EMAIL]" in seen and "[IBAN]" in seen and TOKEN.search(seen)
    assert result.answer == f"Oui pour {NAME}."


def test_an_empty_vault_still_protects_the_question():
    # The request vault is empty (so falsy) when the question is protected: it must be
    # tested with "is not None", never with "if vault".
    _, retriever, _, _ = run(f"Écrire à {EMAIL}")
    assert retriever.questions == ["Écrire à [EMAIL]"]


def test_the_gateway_never_tests_a_vault_for_truth():
    source = inspect.getsource(llm_gateway)
    assert not re.search(r"\bif\s+(not\s+)?vault\b", source)


def test_one_shot_known_values_protect_question_and_extracts():
    result, retriever, _, provider = run(
        f"Où est {NAME} ?",
        chunks=[chunk(f"{NAME} est suivie par le Front Office.")],
        known_values={EntityType.PERSON: (name for name in [NAME])},
    )
    assert NAME not in retriever.questions[0]
    assert NAME not in provider.prompts[0]
    assert result.report.outcome is Outcome.ANSWERED


# --- Pas d'appel au fournisseur (D4, D6) -------------------------------------------------


@pytest.mark.parametrize(
    "chunks",
    [(), [chunk("MARQUEUR_B.", layer=OTHER_LAYER)], [chunk("Note.", source=C3_SOURCE)]],
)
def test_no_authorised_context_means_no_provider_call(chunks):
    result, _, assembler, provider = run(chunks=chunks)
    assert result.answer is None
    assert result.report.outcome is Outcome.NO_AUTHORIZED_CONTEXT
    assert assembler.calls == [] and provider.prompts == []


def test_a_prompt_over_the_limit_is_refused_without_provider_call():
    options, _, assembler, provider = gateway()
    size = len(assembler("Quelle est la procédure ?", ()))  # size without the extract
    assembler.calls.clear()
    full = size + len("\n[doc_a.pdf] Procédure A.")
    refused = ask("Quelle est la procédure ?", **{**options, "max_prompt_chars": full - 1})
    assert refused.answer is None
    assert refused.report.outcome is Outcome.PROMPT_TOO_LONG
    assert refused.report.prompt_chars == full
    assert provider.prompts == []
    accepted = ask("Quelle est la procédure ?", **{**options, "max_prompt_chars": full})
    assert accepted.report.outcome is Outcome.ANSWERED
    assert len(provider.prompts[0]) == full  # sent whole, never truncated


# --- Restauration limitée aux jetons du prompt envoyé ------------------------------------


def test_only_tokens_of_the_sent_prompt_are_restored():
    other = PseudonymVault()
    foreign = other.protect(NAME, {EntityType.PERSON: [NAME]})

    def reply(prompt):
        sent = TOKEN.search(prompt).group()
        guessed = sent.replace("PERSONNE_1_", "PERSONNE_2_")  # extract discarded for size
        invented = sent.replace("PERSONNE_1_", "PERSONNE_9_")
        return f"{sent} | {guessed} | {invented} | {foreign}"

    result, _, _, provider = run(
        "Qui suit le dossier ?",
        chunks=[
            chunk(f"Voir {NAME}.", score=2),
            chunk("Dossier de Jean Mbarga. " + "x" * 300, score=1),
        ],
        known_values={EntityType.PERSON: [NAME, "Jean Mbarga"]},
        max_context_chars=100,
        provider=FakeProvider(reply),
    )
    assert "Jean" not in provider.prompts[0]
    restored, guessed, invented, kept_foreign = result.answer.split(" | ")
    assert restored == NAME
    assert TOKEN.fullmatch(guessed) and TOKEN.fullmatch(invented)
    assert kept_foreign == foreign
    assert "Jean" not in result.answer
    assert (result.report.tokens_restored, result.report.tokens_withheld) == (1, 3)


def test_a_token_of_a_previous_request_is_never_restored():
    first, _, _, _ = run(
        f"Où est {NAME} ?",
        known_values={EntityType.PERSON: [NAME]},
        provider=FakeProvider(lambda prompt: TOKEN.search(prompt).group()),
    )
    previous = None

    def reply(prompt):
        nonlocal previous
        previous = TOKEN.search(prompt).group()
        return previous

    second, _, _, _ = run(
        f"Où est {NAME} ?", known_values={EntityType.PERSON: [NAME]}, provider=FakeProvider(reply)
    )
    assert first.answer == second.answer == NAME
    # The token of the second request is unknown to any later request.
    third, _, _, _ = run(provider=FakeProvider(lambda prompt: f"Voir {previous}."))
    assert third.answer == f"Voir {previous}."
    assert third.report.tokens_withheld == 1


def test_restore_is_called_once_per_answer(monkeypatch):
    calls = []

    class SpyVault(PseudonymVault):
        def restore(self, text):
            calls.append(1)
            return super().restore(text)

    monkeypatch.setattr(llm_gateway, "PseudonymVault", SpyVault)
    result, _, _, _ = run(
        f"Où est {NAME} ?",
        known_values={EntityType.PERSON: [NAME]},
        provider=FakeProvider(lambda prompt: " ".join([TOKEN.search(prompt).group()] * 3)),
    )
    assert calls == [1]
    assert result.answer == " ".join([NAME] * 3)


def test_a_restored_value_is_not_restored_again():
    # The answer quotes a literal token of a previous request next to a valid token:
    # the single pass restores the valid token only.
    old = PseudonymVault().protect(NAME, {EntityType.PERSON: [NAME]})
    result, _, _, _ = run(
        f"Où est {NAME} ?",
        known_values={EntityType.PERSON: [NAME]},
        provider=FakeProvider(lambda prompt: f"{TOKEN.search(prompt).group()} {old}"),
    )
    assert result.answer == f"{NAME} {old}"


# --- Table abandonnée après succès et après toute erreur ---------------------------------


def _tracked_vaults(monkeypatch):
    refs = []

    class TrackedVault(PseudonymVault):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            refs.append(weakref.ref(self))

    monkeypatch.setattr(llm_gateway, "PseudonymVault", TrackedVault)
    return refs


def test_the_vault_is_dropped_after_a_successful_call(monkeypatch):
    refs = _tracked_vaults(monkeypatch)
    result, _, _, _ = run(known_values={EntityType.PERSON: [NAME]})
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    assert result.answer == "Réponse."


@pytest.mark.parametrize(
    ("overrides", "stage"),
    [
        (
            {"retriever": lambda q: (_ for _ in ()).throw(RuntimeError(f"{NAME} {EMAIL}"))},
            Stage.RETRIEVAL,
        ),
        ({"retriever": lambda q: [RetrievedChunk(NAME, {"source": 1})]}, Stage.CONTEXT),
        ({"retriever": lambda q: f"{NAME}"}, Stage.CONTEXT),
        ({"assembler": lambda q, e: 42}, Stage.ASSEMBLY),
        ({"assembler": lambda q, e: (_ for _ in ()).throw(ValueError(NAME))}, Stage.ASSEMBLY),
        ({"provider": FakeProvider(error=TimeoutError(f"timeout for {NAME}"))}, Stage.PROVIDER),
        ({"provider": FakeProvider(lambda prompt: None)}, Stage.PROVIDER),
    ],
)
def test_any_failure_drops_the_vault_and_hides_the_original_error(monkeypatch, overrides, stage):
    refs = _tracked_vaults(monkeypatch)
    options, _, _, _ = gateway(f"Où est {NAME} ?", **overrides)
    with pytest.raises(GatewayError) as caught:
        ask(f"Où est {NAME} ?", known_values={EntityType.PERSON: [NAME]}, **options)
    error = caught.value
    assert error.report.outcome is Outcome.FAILED
    assert error.report.failed_stage is stage
    assert error.__cause__ is None and error.__context__ is None
    for exposed in (str(error), repr(error), repr(error.report)):
        assert "Awa" not in exposed and "example.org" not in exposed
    del caught, error
    gc.collect()
    assert refs and all(ref() is None for ref in refs)


def test_an_error_kept_by_a_component_does_not_keep_the_vault_alive(monkeypatch):
    # The provider keeps the exception it raises: its traceback must not hold the
    # gateway frames, hence neither the vault nor the question.
    refs = _tracked_vaults(monkeypatch)
    kept = RuntimeError("provider down")
    options, _, _, provider = gateway(provider=FakeProvider(error=kept))
    with pytest.raises(GatewayError):
        ask(f"Où est {NAME} ?", known_values={EntityType.PERSON: [NAME]}, **options)
    gc.collect()
    assert provider.error is kept and kept.__traceback__ is None
    assert refs and all(ref() is None for ref in refs)


def test_a_failure_after_retrieval_keeps_the_counters():
    options, _, _, _ = gateway(provider=FakeProvider(error=RuntimeError("down")))
    with pytest.raises(GatewayError) as caught:
        ask("Quelle est la procédure ?", **options)
    report = caught.value.report
    assert report.failed_stage is Stage.PROVIDER
    assert report.error_type == "RuntimeError"
    assert report.context.retained == 1 and report.prompt_chars > 0


# --- Compte rendu sans contenu (D7) ------------------------------------------------------


def test_the_report_holds_no_content():
    question = f"Le dossier de {NAME} ({EMAIL}) ?"
    result, _, _, provider = run(
        question,
        chunks=[chunk(f"MARQUEUR_A {NAME}.", page=1)],
        known_values={EntityType.PERSON: [NAME]},
        provider=FakeProvider(lambda prompt: TOKEN.search(prompt).group()),
        caller_id=7,
    )
    exposed = " ".join([repr(result), str(result), repr(result.report)])
    for value in ("Awa", "example.org", "MARQUEUR_A", "doc_a.pdf", provider.prompts[0]):
        assert value not in exposed
    assert TOKEN.search(exposed) is None
    assert not hasattr(result.report, "question_chars")  # decision H8 is still open


# --- Entrées invalides : refus avant tout appel ------------------------------------------


@pytest.mark.parametrize(
    ("argument", "value", "error"),
    [
        ("question", None, TypeError),
        ("question", b"question", TypeError),
        ("question", "   ", ValueError),
        ("max_prompt_chars", 0, ValueError),
        ("max_prompt_chars", True, TypeError),
        ("max_prompt_chars", 10.0, TypeError),
        ("max_prompt_chars", None, TypeError),
        ("timeout_seconds", 0, ValueError),
        ("timeout_seconds", -1, ValueError),
        ("timeout_seconds", math.nan, ValueError),
        ("timeout_seconds", math.inf, ValueError),
        ("timeout_seconds", "30", TypeError),
        ("timeout_seconds", True, TypeError),
        ("retriever", None, TypeError),
        ("assembler", "assembler", TypeError),
        ("provider", object(), TypeError),
        ("caller_id", EMAIL, TypeError),
        ("caller_id", True, TypeError),
        ("allowed_layers", LAYER, TypeError),
        ("allowed_layers", [], ValueError),
        ("excluded_sources", [], ValueError),
        ("max_extracts", 0, ValueError),
        ("max_context_chars", None, TypeError),
        ("include_classification", "yes", TypeError),
        ("known_values", {EntityType.PERSON: NAME}, TypeError),
        ("known_values", [NAME], TypeError),
    ],
)
def test_invalid_arguments_are_refused_before_any_call(argument, value, error):
    options, retriever, assembler, provider = gateway()
    question = "Quelle est la procédure ?"
    if argument == "question":
        question = value
    else:
        options[argument] = value
    with pytest.raises(error) as caught:
        ask(question, **options)
    assert retriever.questions == [] and assembler.calls == [] and provider.prompts == []
    assert "Awa" not in str(caught.value) and "example.org" not in str(caught.value)


@pytest.mark.parametrize(
    "argument",
    [
        "allowed_layers",
        "excluded_sources",
        "max_extracts",
        "max_context_chars",
        "max_prompt_chars",
        "timeout_seconds",
        "retriever",
        "assembler",
        "provider",
    ],
)
def test_required_arguments_have_no_default(argument):
    options, _, _, _ = gateway()
    del options[argument]
    with pytest.raises(TypeError, match=argument):
        ask("Quelle est la procédure ?", **options)


# --- Revue V1 à V7 -----------------------------------------------------------------------

SECRET_NAME = "Awa Ndiaye"
SECRET_ID = "XYZ-123"


def _secret_question():
    # Built at run time so that the value is not shared with any constant of the tests.
    return f"Le dossier de {SECRET_NAME} ({SECRET_ID}) est-il complet ?"


def _gateway_frames(frame):
    """Frames of the gateway module in the chain ending at ``frame``."""
    frames = []
    while frame is not None:
        if frame.f_code.co_filename == llm_gateway.__file__:
            frames.append(frame)
        frame = frame.f_back
    return frames


def _traceback_frames(tb):
    frames = []
    while tb is not None:
        frames.append(tb.tb_frame)
        tb = tb.tb_next
    return frames


def _assert_no_secret(frames):
    assert frames, "no gateway frame to inspect"
    for frame in frames:
        for name, value in frame.f_locals.items():
            # The raw question and the known value; the protected question, which is
            # what the provider receives, may legitimately remain.
            assert _secret_question() not in repr(value), (frame.f_code.co_name, name)
            assert SECRET_NAME not in repr(value), (frame.f_code.co_name, name)
            assert not isinstance(value, PseudonymVault), (frame.f_code.co_name, name)


def _ask_secret(**overrides):
    options, _, _, _ = gateway(**overrides)
    return ask(_secret_question(), known_values={EntityType.PERSON: [SECRET_NAME]}, **options)


def test_v1_gateway_error_frames_hold_no_raw_question_nor_known_values():
    with pytest.raises(GatewayError) as caught:
        _ask_secret(provider=FakeProvider(error=RuntimeError("down")))
    frames = [
        f
        for f in _traceback_frames(caught.value.__traceback__)
        if f.f_code.co_filename == llm_gateway.__file__
    ]
    _assert_no_secret(frames)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_v2_a_base_exception_is_re_raised_unchanged_and_kept_safely(monkeypatch, interruption):
    refs = _tracked_vaults(monkeypatch)
    kept = interruption()
    with pytest.raises(interruption) as caught:
        _ask_secret(provider=FakeProvider(error=kept))
    assert caught.value is kept  # neither intercepted nor replaced
    assert kept.__traceback__ is not None  # the traceback is kept for the caller
    _assert_no_secret(
        [
            f
            for f in _traceback_frames(kept.__traceback__)
            if f.f_code.co_filename == llm_gateway.__file__
        ]
    )
    gc.collect()
    assert refs and all(ref() is None for ref in refs)


def _frame_keeper(answer="Réponse.", error=None):
    kept = []

    class KeepingProvider:
        def complete(self, prompt, *, timeout_seconds):
            kept.append(sys._getframe())
            if error is not None:
                raise error
            return answer

    return KeepingProvider(), kept


def test_v3_a_frame_kept_by_the_provider_holds_nothing_after_success(monkeypatch):
    refs = _tracked_vaults(monkeypatch)
    provider, kept = _frame_keeper()
    result = _ask_secret(provider=provider)
    assert result.answer == "Réponse."
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    _assert_no_secret(_gateway_frames(kept[0]))


def test_v3_a_frame_kept_by_the_provider_holds_nothing_after_an_error(monkeypatch):
    refs = _tracked_vaults(monkeypatch)
    provider, kept = _frame_keeper(error=RuntimeError("down"))
    with pytest.raises(GatewayError):
        _ask_secret(provider=provider)
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    _assert_no_secret(_gateway_frames(kept[0]))


def test_v3_a_frame_kept_by_the_retriever_holds_nothing(monkeypatch):
    refs = _tracked_vaults(monkeypatch)
    kept = []

    def retriever(question):
        kept.append(sys._getframe())
        yield chunk(f"Procédure suivie par {SECRET_NAME}.")

    _ask_secret(retriever=retriever)
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    _assert_no_secret(_gateway_frames(kept[0]))


def test_v3_the_restored_answer_is_only_in_the_result(monkeypatch):
    kept = []
    provider_answer = []

    class EchoProvider:
        def complete(self, prompt, *, timeout_seconds):
            kept.append(sys._getframe())
            provider_answer.append(TOKEN.search(prompt).group())
            return provider_answer[0]

    result = _ask_secret(provider=EchoProvider())
    assert result.answer == SECRET_NAME
    _assert_no_secret(_gateway_frames(kept[-1]))


def test_v5_custom_error_class_names_never_reach_the_report():
    leaky = type(f"Err_{SECRET_NAME.replace(' ', '_')}", (Exception,), {})
    spoofed = type("RuntimeError", (Exception,), {})  # same name as a built-in
    for error, expected in ((leaky(), "ComponentError"), (spoofed(), "ComponentError")):
        with pytest.raises(GatewayError) as caught:
            _ask_secret(provider=FakeProvider(error=error))
        assert caught.value.report.error_type == expected
    with pytest.raises(GatewayError) as caught:
        _ask_secret(provider=FakeProvider(error=TimeoutError()))
    assert caught.value.report.error_type == "TimeoutError"


def test_v6_a_retriever_failing_while_iterated_is_a_retrieval_failure():
    def retriever(question):
        yield chunk("Procédure.")
        raise ValueError("retriever broke")

    with pytest.raises(GatewayError) as caught:
        _ask_secret(retriever=retriever)
    assert caught.value.report.failed_stage is Stage.RETRIEVAL
    assert caught.value.report.error_type == "ValueError"


def test_v7_a_foreign_token_in_the_question_is_not_counted_as_restored():
    foreign = PseudonymVault().protect("Jean Mbarga", {EntityType.PERSON: ["Jean Mbarga"]})
    result, _, _, provider = run(
        f"Qui est {foreign} ?", provider=FakeProvider(lambda prompt: f"{foreign} inconnu")
    )
    assert foreign in provider.prompts[0]
    assert result.answer == f"{foreign} inconnu"
    assert (result.report.tokens_restored, result.report.tokens_withheld) == (0, 0)


def test_v7_only_actually_replaced_tokens_are_counted():
    result = _ask_secret(
        provider=FakeProvider(lambda prompt: " / ".join([TOKEN.search(prompt).group()] * 2))
    )
    assert result.answer == f"{SECRET_NAME} / {SECRET_NAME}"
    assert result.report.tokens_restored == 2


# --- Portées fournies par un itérable à usage unique --------------------------------------

SCOPE_CHUNKS = [chunk("MARQUEUR_A autorisé."), chunk("MARQUEUR_C3 exclu.", source=C3_SOURCE)]


def _scope_outcome(**scopes):
    result, _, assembler, provider = run(chunks=SCOPE_CHUNKS, **scopes)
    return (
        result.answer,
        result.report.outcome,
        result.report.context,
        provider.prompts,
        [[e.text for e in extracts] for _, extracts in assembler.calls],
    )


@pytest.mark.parametrize(
    "scopes",
    [
        {"allowed_layers": (layer for layer in [LAYER])},
        {"excluded_sources": (source for source in [C3_SOURCE])},
        {
            "allowed_layers": (layer for layer in [LAYER]),
            "excluded_sources": (source for source in [C3_SOURCE]),
        },
        {"allowed_layers": iter([LAYER]), "excluded_sources": iter([C3_SOURCE])},
    ],
)
def test_one_shot_scopes_filter_exactly_like_lists(scopes):
    expected = _scope_outcome(allowed_layers=[LAYER], excluded_sources=[C3_SOURCE])
    assert _scope_outcome(**scopes) == expected
    answer, outcome, context, prompts, extracts = expected
    assert outcome is Outcome.ANSWERED and answer == "Réponse."
    assert context.retained == 1 and dict(context.excluded) == {ExclusionReason.EXCLUDED_SOURCE: 1}
    assert extracts == [["MARQUEUR_A autorisé."]]
    assert "MARQUEUR_C3" not in prompts[0]


def test_one_shot_scopes_are_read_only_once():
    reads = []

    def scope(values, name):
        reads.append(name)
        yield from values

    run(
        chunks=SCOPE_CHUNKS,
        allowed_layers=scope([LAYER], "allowed_layers"),
        excluded_sources=scope([C3_SOURCE], "excluded_sources"),
    )
    assert reads == ["allowed_layers", "excluded_sources"]


@pytest.mark.parametrize("argument", ["allowed_layers", "excluded_sources"])
@pytest.mark.parametrize(
    ("make", "error"),
    [
        (lambda: "layer_a", TypeError),  # a lone string is never read as a collection
        (lambda: b"layer_a", TypeError),
        (lambda: {"layer_a": 1}, TypeError),  # a mapping stays refused
        (lambda: 3, TypeError),  # not iterable
        (lambda: None, TypeError),
        (lambda: (value for value in []), ValueError),  # empty
        (lambda: (value for value in ["layer_a", None]), TypeError),
        (lambda: (value for value in ["layer_a", " "]), ValueError),
    ],
)
def test_invalid_scopes_are_still_refused_before_any_call(argument, make, error):
    # A fresh value per case: a generator shared between cases would already be empty.
    options, retriever, assembler, provider = gateway()
    options[argument] = make()
    with pytest.raises(error, match=argument):
        ask("Quelle est la procédure ?", **options)
    assert retriever.questions == [] and assembler.calls == [] and provider.prompts == []
