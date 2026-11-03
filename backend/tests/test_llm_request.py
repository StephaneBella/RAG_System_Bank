# US 07 : contexte partagé d'une requête LLM (préparation de l'agent RAG), testé sans
# fournisseur ni agent. Un faux adaptateur sans état récupère le contexte actif à chaque
# appel, comme le feront les adaptateurs LangChain et LlamaIndex.

import asyncio
import gc
import re
import threading
import weakref

import pytest

from app.services import llm_request
from app.services.llm_request import (
    CallKind,
    CallLimitExceeded,
    CallOutcome,
    LLMRequestContext,
    NoActiveRequest,
    PromptTooLong,
    ProviderCallReport,
    RequestClosed,
    RequestLimits,
    bind_request,
    require_request,
)
from app.services.pseudonymization import EntityType, PseudonymVault

NAME = "Awa Ndiaye"
OTHER_NAME = "Jean Mbarga"
IBAN = "FR76 3000 6000 0112 3456 7890 189"
TOKEN = re.compile(r"\[(PERSONNE|CLIENT|DOSSIER)_(\d+)_([0-9a-f]{32})\]")
KNOWN = {EntityType.PERSON: [NAME, OTHER_NAME]}


def limits(**overrides):
    values = {"max_provider_calls": 5, "max_prompt_chars": 2000, "timeout_seconds": 30.0}
    return RequestLimits(**{**values, **overrides})


def context(caller_id=7, **overrides):
    return LLMRequestContext(caller_id=caller_id, limits=limits(**overrides), known_values=KNOWN)


class StatelessAdapter:
    """Shape of a future LLM adapter: no user, vault or context kept between calls."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, kind, text):
        request = require_request()  # fetched at the moment of the call
        with request.call(kind) as call:
            prompt = request.protect(text)
            call.sent(prompt)
            self.prompts.append(prompt)
            return call.restore(self.reply(prompt))


def echo(prompt):
    return prompt


# --- Échec sécurisé sans contexte actif ---------------------------------------------------


def test_require_request_fails_closed_without_an_active_context():
    with pytest.raises(NoActiveRequest):
        require_request()


def test_an_adapter_cannot_reach_a_provider_outside_a_request():
    adapter = StatelessAdapter(echo)
    with pytest.raises(NoActiveRequest):
        adapter(CallKind.AGENT_STEP, f"Où est {NAME} ?")
    assert adapter.prompts == []


def test_require_request_returns_the_entered_context_then_fails_again():
    with context() as request:
        assert require_request() is request
    with pytest.raises(NoActiveRequest):
        require_request()


def test_a_closed_context_still_bound_is_refused():
    request = context()
    with bind_request(request):
        request.close()
        with pytest.raises(RequestClosed):
            require_request()


def test_a_closed_context_cannot_be_bound_nor_entered():
    request = context()
    request.close()
    with pytest.raises(RequestClosed), bind_request(request):
        pass
    with pytest.raises(RequestClosed), request:
        pass


def test_a_context_cannot_be_entered_twice():
    with context() as request:
        with pytest.raises(RuntimeError), request:
            pass
        assert require_request() is request


# --- Isolation entre requêtes -------------------------------------------------------------


def test_each_request_has_its_own_server_side_request_id():
    first, second = context(), context()
    assert re.fullmatch(r"[0-9a-f]{32}", first.request_id)
    assert first.request_id != second.request_id  # same caller, same moment


def test_each_request_has_its_own_vault():
    with context() as first:
        token_a = first.protect(NAME)
    with context() as second:
        token_b = second.protect(NAME)
    assert TOKEN.fullmatch(token_a) and TOKEN.fullmatch(token_b)
    assert token_a != token_b


def test_a_token_of_another_request_is_never_restored():
    with context() as first:
        foreign = first.protect(NAME)
    with context() as second, second.call(CallKind.AGENT_STEP) as call:
        call.sent(second.protect(f"Question sur {NAME}"))
        assert call.restore(f"Voir {foreign}") == f"Voir {foreign}"
    assert second.reports()[0].tokens_withheld == 1


def test_a_stateless_adapter_uses_the_vault_of_each_request():
    adapter = StatelessAdapter(echo)
    answers = []
    for _ in range(2):
        with context():
            answers.append(adapter(CallKind.AGENT_STEP, f"Où est {NAME} ?"))
    assert answers == [f"Où est {NAME} ?"] * 2
    tokens = [TOKEN.search(prompt).group() for prompt in adapter.prompts]
    assert NAME not in "".join(adapter.prompts)
    assert tokens[0] != tokens[1]
    assert not hasattr(adapter, "request") and not hasattr(adapter, "vault")


# --- Protection et restauration -----------------------------------------------------------


def test_protect_masks_and_pseudonymises_with_the_known_values():
    with context() as request:
        protected = request.protect(f"{NAME}, IBAN {IBAN}")
    assert NAME not in protected and IBAN not in protected
    assert TOKEN.search(protected)


def test_restore_only_puts_back_the_tokens_sent_by_this_call():
    with context() as request:
        sent, not_sent = request.protect(NAME), request.protect(OTHER_NAME)
        with request.call(CallKind.AGENT_STEP) as call:
            call.sent(f"Question sur {sent}")
            text = call.restore(f"{sent} et {not_sent}")
    assert text == f"{NAME} et {not_sent}"
    assert OTHER_NAME not in text
    report = request.reports()[0]
    assert (report.tokens_sent, report.tokens_restored, report.tokens_withheld) == (1, 1, 1)


def test_a_token_sent_by_a_previous_call_is_not_restored_by_the_next_one():
    with context() as request:
        token = request.protect(NAME)
        with request.call(CallKind.QUERY_REWRITE) as call:
            call.sent(token)
            assert call.restore(token) == NAME
        with request.call(CallKind.AGENT_STEP) as call:
            call.sent("Sans nom.")
            assert call.restore(token) == token


def test_restore_counts_add_up_over_several_parts_of_an_answer():
    with context() as request:
        token = request.protect(NAME)
        with request.call(CallKind.AGENT_STEP) as call:
            call.sent("Système.", f"Question sur {token}")
            assert call.restore(f"Réponse : {token}") == f"Réponse : {NAME}"
            assert call.restore(f'{{"query": "{token}"}}') == f'{{"query": "{NAME}"}}'
    report = request.reports()[0]
    assert report.tokens_restored == 2
    assert report.prompt_chars == len("Système.") + len(f"Question sur {token}")
    assert report.answer_chars == len(f"Réponse : {token}") + len(f'{{"query": "{token}"}}')


def test_restore_needs_sent_and_sent_is_once_per_call():
    with context() as request:
        with (
            pytest.raises(RuntimeError, match="sent"),
            request.call(CallKind.AGENT_STEP) as call,
        ):
            call.restore("x")
        with (
            pytest.raises(RuntimeError, match="once"),
            request.call(CallKind.AGENT_STEP) as call,
        ):
            call.sent("a")
            call.sent("b")


def test_invalid_known_values_are_refused_at_creation():
    for known in ({"PERSONNE": [NAME]}, {EntityType.PERSON: NAME}, NAME):
        with pytest.raises(TypeError):
            LLMRequestContext(caller_id=1, limits=limits(), known_values=known)


def test_known_values_given_as_a_generator_are_read_once():
    request = LLMRequestContext(
        caller_id=1, limits=limits(), known_values={EntityType.PERSON: (n for n in [NAME])}
    )
    with request:
        assert NAME not in request.protect(NAME)
        assert NAME not in request.protect(NAME)


@pytest.mark.parametrize("caller_id", [None, True, "7", 7.0])
def test_caller_id_must_be_an_int(caller_id):
    with pytest.raises(TypeError):
        LLMRequestContext(caller_id=caller_id, limits=limits())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_provider_calls": 0},
        {"max_provider_calls": True},
        {"max_prompt_chars": -1},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "30"},
    ],
)
def test_invalid_limits_are_refused(overrides):
    with pytest.raises((TypeError, ValueError)):
        limits(**overrides)


# --- Suivi des appels ---------------------------------------------------------------------


def test_calls_are_indexed_and_reported_in_order():
    adapter = StatelessAdapter(echo)
    kinds = [CallKind.AGENT_STEP, CallKind.QUERY_REWRITE, CallKind.SELF_QUERY]
    with context() as request:
        for kind in kinds:
            adapter(kind, f"Où est {NAME} ?")
    reports = request.reports()
    assert [r.call_index for r in reports] == [0, 1, 2]
    assert [r.kind for r in reports] == kinds
    assert {r.request_id for r in reports} == {request.request_id}
    assert all(r.caller_id == 7 and r.outcome is CallOutcome.COMPLETED for r in reports)
    assert all(r.error_type is None and r.duration_ms >= 0 for r in reports)


def test_reports_hold_no_text():
    adapter = StatelessAdapter(lambda prompt: f"Réponse sur {TOKEN.search(prompt).group()}")
    with context() as request:
        adapter(CallKind.AGENT_STEP, f"Le dossier de {NAME}, IBAN {IBAN} ?")
    dumped = repr(request.reports())
    for value in (NAME, IBAN, "dossier", "Réponse", "PERSONNE"):
        assert value not in dumped, value
    for name, field_type in ProviderCallReport.__annotations__.items():
        assert "str" not in str(field_type) or name in ("request_id", "error_type"), name


def test_a_failing_call_is_reported_with_a_safe_error_type_and_re_raised():
    leaky = type(f"Err_{NAME.replace(' ', '_')}", (Exception,), {})
    with context() as request:
        with pytest.raises(leaky), request.call(CallKind.QUERY_FUSION) as call:
            call.sent("Question.")
            raise leaky(NAME)
        with pytest.raises(TimeoutError), request.call(CallKind.AGENT_STEP) as call:
            call.sent("Question.")
            raise TimeoutError
    failed, timeout = request.reports()
    assert (failed.outcome, failed.error_type) == (CallOutcome.FAILED, "ComponentError")
    assert (timeout.outcome, timeout.error_type) == (CallOutcome.FAILED, "TimeoutError")
    assert NAME not in repr(request.reports())


def test_a_prompt_over_the_limit_is_refused_before_sending():
    sent_to_provider = []
    with (
        context(max_prompt_chars=10) as request,
        pytest.raises(PromptTooLong),
        request.call(CallKind.AGENT_STEP) as call,
    ):
        call.sent("x" * 6, "y" * 5)
        sent_to_provider.append(True)  # never reached
    assert sent_to_provider == []
    [report] = request.reports()
    assert report.outcome is CallOutcome.PROMPT_TOO_LONG
    assert (report.prompt_chars, report.tokens_sent, report.error_type) == (11, 0, None)


def test_a_call_ending_without_sent_is_a_failure():
    with (
        context() as request,
        pytest.raises(RuntimeError, match="without sent"),
        request.call(CallKind.AGENT_STEP),
    ):
        pass
    assert request.reports()[0].outcome is CallOutcome.FAILED


def test_a_call_cannot_be_used_after_its_block():
    with context() as request:
        with request.call(CallKind.AGENT_STEP) as call:
            call.sent("Question.")
        with pytest.raises(RequestClosed):
            call.restore("Réponse.")


# --- Limite du nombre d'appels ------------------------------------------------------------


def test_calls_over_the_limit_are_refused_and_reported():
    adapter = StatelessAdapter(echo)
    with context(max_provider_calls=2) as request:
        adapter(CallKind.AGENT_STEP, "Un.")
        adapter(CallKind.QUERY_REWRITE, "Deux.")
        with pytest.raises(CallLimitExceeded):
            adapter(CallKind.AGENT_STEP, f"Trois, {NAME}.")
    assert len(adapter.prompts) == 2  # the refused call never reached the provider
    reports = request.reports()
    assert [r.outcome for r in reports] == [
        CallOutcome.COMPLETED,
        CallOutcome.COMPLETED,
        CallOutcome.CALL_LIMIT_EXCEEDED,
    ]
    assert reports[2].call_index == 2 and reports[2].prompt_chars is None


def test_the_limit_holds_under_parallel_calls():
    request = context(max_provider_calls=10)
    adapter = StatelessAdapter(echo)
    errors = []

    def worker():
        with bind_request(request):
            try:
                adapter(CallKind.AGENT_STEP, f"Où est {NAME} ?")
            except CallLimitExceeded:
                errors.append(1)

    threads = [threading.Thread(target=worker) for _ in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    reports = request.reports()
    assert sorted(r.call_index for r in reports) == list(range(30))
    assert sum(r.outcome is CallOutcome.COMPLETED for r in reports) == 10
    assert len(errors) == 20
    # One vault for the whole request: the same value always gets the same token.
    assert len({TOKEN.search(prompt).group() for prompt in adapter.prompts}) == 1


# --- Fils d'exécution et tâches -----------------------------------------------------------


def test_a_thread_does_not_inherit_the_context_unless_bound():
    seen = {}

    def unbound():
        try:
            require_request()
        except NoActiveRequest:
            seen["unbound"] = "refused"

    def bound(request):
        with bind_request(request):
            seen["bound"] = require_request() is request
        try:
            require_request()
        except NoActiveRequest:
            seen["after"] = "refused"

    with context() as request:
        for target, args in ((unbound, ()), (bound, (request,))):
            thread = threading.Thread(target=target, args=args)
            thread.start()
            thread.join()
    assert seen == {"unbound": "refused", "bound": True, "after": "refused"}


def test_an_asyncio_task_sees_the_context_of_its_request():
    async def handler():
        with context() as request:
            found = await asyncio.create_task(asyncio.to_thread(require_request))
            return found is request

    assert asyncio.run(handler())


def test_concurrent_requests_stay_isolated():
    async def one_request(name):
        with context() as request:
            await asyncio.sleep(0)
            token = require_request().protect(name)
            await asyncio.sleep(0)
            assert require_request() is request
            return request.request_id, token

    async def main():
        return await asyncio.gather(one_request(NAME), one_request(NAME))

    (id_a, token_a), (id_b, token_b) = asyncio.run(main())
    assert id_a != id_b and token_a != token_b


# --- Fermeture explicite ------------------------------------------------------------------


def test_close_drops_the_vault_and_refuses_any_later_use(monkeypatch):
    refs = []

    class TrackedVault(PseudonymVault):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            refs.append(weakref.ref(self))

    monkeypatch.setattr(llm_request, "PseudonymVault", TrackedVault)
    with context() as request, request.call(CallKind.AGENT_STEP) as call:
        call.sent(request.protect(NAME))
    assert request.closed
    gc.collect()
    assert refs and all(ref() is None for ref in refs)
    with pytest.raises(RequestClosed):
        request.protect(NAME)
    with pytest.raises(RequestClosed), request.call(CallKind.AGENT_STEP):
        pass
    assert len(request.reports()) == 1  # reports stay available for the audit


def test_the_context_is_closed_even_when_the_request_fails():
    with pytest.raises(ValueError), context() as request:
        raise ValueError("boom")
    assert request.closed
    with pytest.raises(NoActiveRequest):
        require_request()


def test_close_is_idempotent_and_repr_holds_no_content():
    request = context()
    request.protect(NAME)
    request.close()
    request.close()
    assert NAME not in repr(request) and request.request_id not in repr(request)
