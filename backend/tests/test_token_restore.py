# US 07 T3 / T5 : restauration des jetons de pseudonymisation dans une réponse de LLM,
# testée directement, sans passerelle ni fournisseur.
#
# Seuls les jetons présents dans le prompt réellement envoyé sont restaurés ; tout autre
# jeton (autre table, inventé, écarté) reste littéral et est compté comme retenu.

from types import SimpleNamespace

from app.services import token_restore
from app.services.pseudonymization import EntityType, PseudonymVault
from app.services.token_restore import ANY_TOKEN, restore_sent_tokens

NAME = "Awa Ndiaye"
OTHER_NAME = "Jean Mbarga"


def protected(vault, *names):
    """Tokens of ``names`` in ``vault``, in the order given."""
    known = {EntityType.PERSON: list(names)}
    return [vault.protect(name, known) for name in names]


def test_any_token_matches_only_well_formed_pseudonymisation_tokens():
    [token] = protected(PseudonymVault(), NAME)
    assert ANY_TOKEN.fullmatch(token)
    for text in ("[PERSONNE_1]", "[PERSONNE_1_abc]", "[IBAN_1_" + "0" * 32 + "]", "[EMAIL]"):
        assert not ANY_TOKEN.search(text), text


# --- Jetons réellement envoyés ------------------------------------------------------------


def test_only_tokens_of_the_sent_prompt_are_restored():
    vault = PseudonymVault()
    sent, not_sent = protected(vault, NAME, OTHER_NAME)
    text, restored, withheld = restore_sent_tokens(
        vault, f"{sent} et {not_sent}", prompt=f"Question sur {sent}"
    )
    assert text == f"{NAME} et {not_sent}"
    assert OTHER_NAME not in text
    assert (restored, withheld) == (1, 1)


def test_an_answer_without_token_is_returned_unchanged():
    vault = PseudonymVault()
    [sent] = protected(vault, NAME)
    assert restore_sent_tokens(vault, "Aucun nom.", prompt=sent) == ("Aucun nom.", 0, 0)


# --- Comptages ----------------------------------------------------------------------------


def test_each_restored_occurrence_is_counted():
    vault = PseudonymVault()
    [sent] = protected(vault, NAME)
    text, restored, withheld = restore_sent_tokens(vault, f"{sent}, {sent}, {sent}", prompt=sent)
    assert text == f"{NAME}, {NAME}, {NAME}"
    assert (restored, withheld) == (3, 0)


def test_an_invented_token_of_the_same_vault_is_withheld():
    vault = PseudonymVault()
    [sent] = protected(vault, NAME)
    invented = sent.replace("PERSONNE_1_", "PERSONNE_9_")
    text, restored, withheld = restore_sent_tokens(vault, f"{sent} {invented}", prompt=sent)
    assert text == f"{NAME} {invented}"
    assert (restored, withheld) == (1, 1)


def test_a_sent_token_unknown_to_the_vault_is_neither_restored_nor_withheld():
    vault = PseudonymVault()
    [foreign] = protected(PseudonymVault(), NAME)
    text, restored, withheld = restore_sent_tokens(vault, f"{foreign} ?", prompt=foreign)
    assert text == f"{foreign} ?"
    assert (restored, withheld) == (0, 0)


# --- Jeton étranger -----------------------------------------------------------------------


def test_a_token_of_another_vault_is_never_restored():
    vault, other = PseudonymVault(), PseudonymVault()
    [sent] = protected(vault, NAME)
    [foreign] = protected(other, NAME)
    text, restored, withheld = restore_sent_tokens(vault, f"{sent} / {foreign}", prompt=sent)
    assert text == f"{NAME} / {foreign}"
    assert (restored, withheld) == (1, 1)


# --- Appel unique à restore ---------------------------------------------------------------


def test_restore_is_called_once_per_answer():
    calls = []

    class SpyVault(PseudonymVault):
        def restore(self, text):
            calls.append(text)
            return super().restore(text)

    vault = SpyVault()
    sent, not_sent = protected(vault, NAME, OTHER_NAME)
    text, _, _ = restore_sent_tokens(vault, f"{sent} {sent} {not_sent}", prompt=sent)
    assert len(calls) == 1
    # A withheld token never reaches restore: it is replaced by a placeholder first.
    assert not_sent not in calls[0]
    assert text == f"{NAME} {NAME} {not_sent}"


# --- Caractères de balisage préexistants --------------------------------------------------


def test_marker_characters_already_in_the_answer_are_kept_literally():
    vault = PseudonymVault()
    [sent] = protected(vault, NAME)
    noise = "\ue000x:0\ue001 \ue002x:0\ue003 \ue004x:0\ue005 \ue001\ue005"
    text, restored, withheld = restore_sent_tokens(vault, f"{noise} {sent} {noise}", prompt=sent)
    assert text == f"{noise} {NAME} {noise}"
    assert (restored, withheld) == (1, 0)


# --- Collision de nonce -------------------------------------------------------------------


def test_a_nonce_already_present_in_the_answer_is_drawn_again(monkeypatch):
    vault = PseudonymVault()  # created before the patch: its own id stays random
    sent, not_sent = protected(vault, NAME, OTHER_NAME)
    colliding, fresh = "a" * 32, "b" * 32
    drawn = iter([colliding, fresh])
    calls = []

    def token_hex(nbytes):
        calls.append(nbytes)
        return next(drawn)

    # Only the module under test sees the fake generator.
    monkeypatch.setattr(token_restore, "secrets", SimpleNamespace(token_hex=token_hex))
    answer = f"{sent} \ue004{colliding}:0\ue005 {not_sent} \ue000{colliding}:0\ue001"
    text, restored, withheld = restore_sent_tokens(vault, answer, prompt=sent)
    assert calls == [16, 16]
    assert text == f"{NAME} \ue004{colliding}:0\ue005 {not_sent} \ue000{colliding}:0\ue001"
    assert (restored, withheld) == (1, 1)
