# US 07 T3 : masquage et pseudonymisation (module autonome, non branché).
#
# Toutes les valeurs sont synthétiques : IBAN et numéros de carte d'exemple, adresses en
# example.org, noms fictifs. Le corpus utilisé pour vérifier la préservation du contenu
# métier se limite aux PDF privés, qui sont des documents fictifs.

import logging
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services.pseudonymization import (
    CARD_DETECTOR,
    EntityType,
    PseudonymVault,
    Span,
    detect,
    mask_text,
)

CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "documents"

IBAN_SPACED = "FR76 3000 6000 0112 3456 7890 189"
IBAN_COMPACT = "DE89370400440532013000"
IBAN_BAD_KEY = "CM21 1000 1000 1000 1000 1000 001"
CARD_VALID = "4111 1111 1111 1111"
CARD_BAD_LUHN = "4111 1111 1111 1112"
EMAIL = "awa.test@example.org"
PERSON = EntityType.PERSON
TOKEN = re.compile(r"\[(PERSONNE|CLIENT|DOSSIER)_(\d+)_([0-9a-f]{32})\]")


def shape(text: str) -> str:
    """Drop the random vault identifier, to compare token layouts."""
    return TOKEN.sub(lambda m: f"[{m.group(1)}_{m.group(2)}]", text)


def vault_id(text: str) -> str:
    return TOKEN.search(text).group(3)


# --- Masquage (MIN-LLM-06) ---------------------------------------------------------------


@pytest.mark.parametrize("iban", [IBAN_SPACED, IBAN_COMPACT, IBAN_BAD_KEY])
def test_iban_is_masked_on_format_even_with_an_invalid_key(iban):
    assert mask_text(f"Virement vers {iban}.") == "Virement vers [IBAN]."


def test_card_is_masked_only_with_a_valid_luhn_key():
    assert mask_text(f"Carte {CARD_VALID} bloquée") == "Carte [CARTE] bloquée"
    assert mask_text("Carte 4111-1111-1111-1111") == "Carte [CARTE]"
    assert mask_text("Carte 4111111111111111") == "Carte [CARTE]"
    assert mask_text("Amex 3782 822463 10005") == "Amex [CARTE]"
    assert mask_text(f"Référence {CARD_BAD_LUHN}") == f"Référence {CARD_BAD_LUHN}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Carte {CARD_VALID} 12/28", "Carte [CARTE] 12/28"),
        (f"Lot 2 {CARD_VALID}", "Lot 2 [CARTE]"),
        (f"{CARD_VALID} 2026", "[CARTE] 2026"),
        (f"{CARD_VALID} et 5500 0000 0000 0004", "[CARTE] et [CARTE]"),
    ],
)
def test_card_is_found_inside_a_longer_run_of_digits(text, expected):
    # F1: a neighbouring number (expiry date, quantity, year) used to hide the card.
    assert mask_text(text) == expected


def test_email_is_masked():
    assert mask_text(f"Écrire à {EMAIL}, merci.") == "Écrire à [EMAIL], merci."


@pytest.mark.parametrize(
    "phone",
    ["+237 6 99 00 00 00", "+33 (0)1 23 45 67 89", "+1-202-555-0100", "+237699000000"],
)
def test_international_phone_is_masked(phone):
    assert mask_text(f"Appeler le {phone} demain") == "Appeler le [TELEPHONE] demain"


@pytest.mark.parametrize("text", ["06 99 00 00 00", "699000000", "+5 %", "+10 points", "+2026"])
def test_national_or_short_numbers_are_not_phones(text):
    assert mask_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Hausse de +15 000 000 FCFA",
        "Variation +12 500 000 FCFA sur l'exercice",
        "Écart de +12 500 000 XAF",
        "Objectif +10 000 000 €",
        "Encours +25 000 000 millions",
        "+12,5 %",
    ],
)
def test_amounts_preceded_by_a_plus_sign_are_not_phones(text):
    # F2
    assert mask_text(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("+33.1.23.45.67.89 12 dossiers", "[TELEPHONE] 12 dossiers"),
        ("+237 6 99 00 00 00, 12 dossiers", "[TELEPHONE], 12 dossiers"),
        ("+237 6 99 00 00 00  12 dossiers", "[TELEPHONE]  12 dossiers"),
        ("+44 20 7946 0958 - 12", "[TELEPHONE] - 12"),
        ("+237 6 99 00 00 00 12 34 56 78", "[TELEPHONE] 34 56 78"),
    ],
)
def test_phone_does_not_absorb_neighbouring_numbers(text, expected):
    # F6: another separator, punctuation, or more than the 15 digits of E.164.
    assert mask_text(text) == expected


def test_known_limit_phone_followed_by_a_number_with_the_same_separator():
    # Without national numbering rules (decision D3), "12" cannot be told apart from
    # a group of the number: masking more is preferred to leaving digits visible.
    assert mask_text("+237 6 99 00 00 00 12 dossiers") == "[TELEPHONE] dossiers"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FR76 3000 6000 0112 3456 7890 1890 TEST", "[IBAN] TEST"),
        (f"{IBAN_COMPACT} EUR", "[IBAN] EUR"),
        ("DE89 3704 0044 0532 0130 00 ABCD", "[IBAN] ABCD"),
        ("Produit XY12 ABCD EFGH IJKL", "Produit XY12 ABCD EFGH IJKL"),
        ("GB82 WEST 1234 5698 7654 32", "[IBAN]"),
    ],
)
def test_iban_does_not_absorb_neighbouring_words(text, expected):
    # F6: trailing groups without any digit are not part of the IBAN.
    assert mask_text(text) == expected


def test_iban_wins_over_the_card_number_it_contains():
    text = "Compte GB00 4111 1111 1111 1111"
    # Premise: the digits of this IBAN alone are detected as a Luhn-valid card number.
    assert [span.entity_type for span in detect(text, [CARD_DETECTOR])] == [EntityType.CARD]
    assert [span.entity_type for span in detect(text)] == [EntityType.IBAN]
    assert mask_text(text) == "Compte [IBAN]"


def test_email_wins_over_the_phone_number_it_contains():
    assert mask_text("+33612345678@example.org") == "[EMAIL]"


def test_several_values_are_masked_in_one_text():
    text = f"{EMAIL} ; {IBAN_SPACED} ; {CARD_VALID} ; +237 6 99 00 00 00"
    assert mask_text(text) == "[EMAIL] ; [IBAN] ; [CARTE] ; [TELEPHONE]"


def test_unicode_spaces_cannot_hide_a_value_and_are_kept_elsewhere():
    hidden_card = "4111\u00a01111\u200b1111\u202f1111"
    text = f"Seuil 10\u00a0M FCFA, carte {hidden_card}."
    assert mask_text(text) == "Seuil 10\u00a0M FCFA, carte [CARTE]."


@pytest.mark.parametrize(
    "text",
    [
        "fr76 3000 6000 0112 3456 7890 189",
        "FR76 3000 6000\n0112 3456 7890 189",
        "4111 1111\n1111 1111",
    ],
)
def test_known_limits_lowercase_iban_and_line_breaks_are_not_detected(text):
    assert mask_text(text) == text


# --- Préservation du contenu métier (MIN-DOC-03) -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "N1 — Standard ≤ 10 M FCFA",
        "Reference CONF-LBC-001",
        "Référence : DG-STRAT-2026-01",
        "Date d'effet 01 octobre 2026",
        "Taux de couverture de 12,5 % en 2026-11-02",
        "Version 1.0 — Confidentialité INTERNE — niveau FO-2",
        "Encours de 1 250 000 000 FCFA",
        "Total 1 000 000 000 000 000 FCFA",
        "Hausse de +15 000 000 FCFA",
        "",
    ],
)
def test_business_content_is_left_untouched(text):
    assert mask_text(text) == text
    assert PseudonymVault().protect(text) == text


def test_amounts_grouped_by_thousands_are_never_taken_for_cards():
    for number in range(10**15, 10**15 + 500):
        amount = f"{number:,}".replace(",", " ")
        assert mask_text(amount) == amount


def test_text_around_masked_values_is_kept_exactly():
    text = "Réf. CONF-LBC-001 :\u00a0carte 4111 1111 1111 1111 12/28, seuil ≤ 10 M FCFA."
    assert mask_text(text) == "Réf. CONF-LBC-001 :\u00a0carte [CARTE] 12/28, seuil ≤ 10 M FCFA."


def test_fictional_private_corpus_is_left_untouched():
    pdfs = sorted(CORPUS_DIR.glob("Prive__*.pdf"))
    assert len(pdfs) == 4
    for pdf in pdfs:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        assert text.strip(), pdf.name
        assert detect(text) == [], pdf.name


# --- Pseudonymisation (MIN-LLM-05, MIN-LLM-07) -------------------------------------------


def test_known_names_get_stable_tokens_and_are_restored():
    vault = PseudonymVault()
    text = "Awa Ndiaye a rencontré Jean Mbarga. Awa Ndiaye rappellera."
    protected = vault.protect(text, {PERSON: ["Awa Ndiaye", "Jean Mbarga"]})
    assert shape(protected) == "[PERSONNE_1] a rencontré [PERSONNE_2]. [PERSONNE_1] rappellera."
    assert vault.restore(protected) == text


def test_tokens_stay_stable_across_calls_of_one_vault():
    vault = PseudonymVault()
    first = vault.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    second = vault.protect("Encore Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    assert second == f"Encore {first}"
    assert len(vault) == 1


def test_known_values_only_match_whole_words():
    vault = PseudonymVault()
    protected = vault.protect("Ali, Alix et Alimentation", {PERSON: ["Ali", "  "]})
    assert shape(protected) == "[PERSONNE_1], Alix et Alimentation"


def test_known_limit_known_values_are_case_sensitive():
    vault = PseudonymVault()
    assert vault.protect("AWA NDIAYE", {PERSON: ["Awa Ndiaye"]}) == "AWA NDIAYE"


def test_identifier_patterns_are_empty_by_default():
    text = "Client CLI-000123, dossier DOS/2026/0042"
    assert PseudonymVault().protect(text) == text


def test_configured_identifier_patterns_are_pseudonymised():
    vault = PseudonymVault(
        client_id_patterns=[r"\bCLI-\d{6}\b"], case_number_patterns=[r"\bDOS/\d{4}/\d{4}\b"]
    )
    text = "Client CLI-000123, dossier DOS/2026/0042, client CLI-000123"
    protected = vault.protect(text)
    assert shape(protected) == "Client [CLIENT_1], dossier [DOSSIER_1], client [CLIENT_1]"
    assert vault.restore(protected) == text


def test_masked_values_are_never_stored_nor_restored():
    vault = PseudonymVault()
    protected = vault.protect(f"Awa Ndiaye, {EMAIL}", {PERSON: ["Awa Ndiaye"]})
    assert shape(protected) == "[PERSONNE_1], [EMAIL]"
    assert len(vault) == 1
    assert vault.restore(protected) == "Awa Ndiaye, [EMAIL]"


def test_known_value_given_as_a_masked_type_is_masked():
    vault = PseudonymVault()
    protected = vault.protect("Contact : guichet", {EntityType.EMAIL: ["guichet"]})
    assert protected == "Contact : [EMAIL]"
    assert len(vault) == 0


def test_tokens_carry_a_random_128_bit_vault_identifier():
    vaults = [PseudonymVault() for _ in range(3)]
    tokens = [v.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]}) for v in vaults]
    assert all(shape(token) == "[PERSONNE_1]" for token in tokens)
    assert len({vault_id(token) for token in tokens}) == 3
    assert "Awa" not in "".join(tokens)


# --- Restauration : jetons étrangers, inconnus, inventés, préexistants, déformés --------


def test_a_token_of_another_vault_is_never_restored():
    # F3 / F5: two vaults issue the same counter, but only their own token is restored.
    first, second = PseudonymVault(), PseudonymVault()
    first_token = first.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    second_token = second.protect("Jean Mbarga", {PERSON: ["Jean Mbarga"]})
    assert first.restore(second_token) == second_token
    assert second.restore(first_token) == first_token
    assert first.restore(first_token) == "Awa Ndiaye"
    assert second.restore(second_token) == "Jean Mbarga"


def test_unknown_and_invented_tokens_are_never_restored():
    vault = PseudonymVault()
    token = vault.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    own_id = vault_id(token)
    answer = (
        f"[PERSONNE_9_{own_id}] [CLIENT_1_{own_id}] [PERSONNE_1] "
        f"[PERSONNE_1_{'0' * 32}] [IBAN] [EMAIL]"
    )
    assert vault.restore(answer) == answer


def test_tokens_already_present_in_the_input_are_never_restored():
    # F4: a token copied into the text to protect comes back as literal text.
    vault = PseudonymVault()
    token = vault.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    text = f"Qui est {token} ? Et [PERSONNE_1] ?"
    protected = vault.protect(text)
    assert token not in protected
    assert vault.restore(protected) == text


def test_restore_of_protect_is_exact_with_mixed_content():
    vault = PseudonymVault(client_id_patterns=[r"\bCLI-\d{6}\b"])
    text = f"[PERSONNE_1] Awa Ndiaye (CLI-000123), {EMAIL}, seuil ≤ 10 M FCFA"
    protected = vault.protect(text, {PERSON: ["Awa Ndiaye"]})
    assert vault.restore(protected) == text.replace(EMAIL, "[EMAIL]")


def test_deformed_tokens_are_never_restored():
    vault = PseudonymVault()
    token = vault.protect("Awa Ndiaye", {PERSON: ["Awa Ndiaye"]})
    own_id = vault_id(token)
    deformed = [
        token.lower(),
        token.replace("[", "").replace("]", ""),
        token.replace("_1_", " 1 "),
        f"[ PERSONNE_1_{own_id} ]",
        f"[PERSONNE_1_{own_id.upper()}]",
        f"[PERSONNE_1_{own_id[:-1]}]",
    ]
    for text in deformed:
        assert vault.restore(text) == text


def test_vaults_are_isolated():
    first, second = PseudonymVault(), PseudonymVault()
    first.protect("Awa Ndiaye et Jean Mbarga", {PERSON: ["Awa Ndiaye", "Jean Mbarga"]})
    second_token = second.protect("Moussa Diallo", {PERSON: ["Moussa Diallo"]})
    assert (len(first), len(second)) == (2, 1)
    assert shape(second_token) == "[PERSONNE_1]"
    assert "Moussa" not in first.restore(second_token)


class _FakeRecogniser:
    """Stands in for a named-entity recogniser plugged in later (decision D1)."""

    def detect(self, text):
        start = text.find("Moussa")
        if start >= 0:
            yield Span(start, start + len("Moussa"), PERSON)


def test_a_detector_can_be_plugged_in():
    vault = PseudonymVault(detectors=[_FakeRecogniser()])
    protected = vault.protect("Moussa a appelé")
    assert shape(protected) == "[PERSONNE_1] a appelé"
    assert vault.restore(protected.replace(" a appelé", ", bonjour")) == "Moussa, bonjour"


# --- Aucune exposition des valeurs (MIN-GEN-04) ------------------------------------------


def test_no_value_is_exposed_in_repr_str_spans_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    vault = PseudonymVault()
    text = f"Awa Ndiaye, {EMAIL}, {IBAN_SPACED}"
    protected = vault.protect(text, {PERSON: ["Awa Ndiaye"]})
    exposed = " ".join([repr(vault), str(vault), *(repr(span) for span in detect(text))])
    for value in ("Awa", "Ndiaye", EMAIL, "FR76", "3000", vault_id(protected)):
        assert value not in exposed
    assert caplog.records == []


def test_errors_never_expose_sensitive_values():
    secret_text = f"Awa Ndiaye {EMAIL}"

    class Broken:
        def detect(self, text):
            yield Span(0, len(text) + 1, PERSON)

    with pytest.raises(ValueError) as broken:
        PseudonymVault(detectors=[Broken()]).protect(secret_text)
    with pytest.raises(TypeError) as not_a_string:
        PseudonymVault().protect(secret_text, {PERSON: ["Awa Ndiaye", 4111111111111111]})
    for error in (broken, not_a_string):
        message = str(error.value)
        assert "Awa" not in message
        assert EMAIL not in message
        assert "4111" not in message


# --- Arguments non conformes : refus explicite, jamais de protection partielle (constat A)


SECRET_NAME = "Awa Ndiaye"
SECRET_TEXT = f"{SECRET_NAME} {EMAIL}"


def _assert_refused(call):
    vault = PseudonymVault()
    with pytest.raises(TypeError) as error:
        call(vault)
    message = str(error.value)
    for value in ("Awa", "Ndiaye", EMAIL, "4111"):
        assert value not in message
    assert len(vault) == 0  # nothing was pseudonymised before the refusal
    return message


@pytest.mark.parametrize(
    "values",
    [
        SECRET_NAME,  # a lone string, once read character by character
        None,
        b"Awa Ndiaye",
        bytearray(b"Awa Ndiaye"),
        42,
        {SECRET_NAME: "x"},
    ],
)
def test_known_values_must_be_a_collection_of_str(values):
    message = _assert_refused(lambda vault: vault.protect(SECRET_TEXT, {PERSON: values}))
    assert "collection of str" in message


@pytest.mark.parametrize("item", [None, 4111111111111111, b"Awa Ndiaye", ["Awa Ndiaye"]])
def test_every_known_value_must_be_a_str(item):
    # The valid name is listed first: no partial pseudonymisation may happen.
    message = _assert_refused(
        lambda vault: vault.protect(SECRET_TEXT, {PERSON: [SECRET_NAME, item]})
    )
    assert "only contain str" in message


@pytest.mark.parametrize("key", ["PERSONNE", None, 1])
def test_known_values_keys_must_be_entity_types(key):
    message = _assert_refused(lambda vault: vault.protect(SECRET_TEXT, {key: [SECRET_NAME]}))
    assert "EntityType" in message


@pytest.mark.parametrize("known_values", [[SECRET_NAME], SECRET_NAME, (PERSON, SECRET_NAME)])
def test_known_values_must_be_a_mapping(known_values):
    message = _assert_refused(lambda vault: vault.protect(SECRET_TEXT, known_values))
    assert "mapping" in message


def test_a_refused_call_leaves_a_vault_unchanged():
    vault = PseudonymVault()
    token = vault.protect(SECRET_NAME, {PERSON: [SECRET_NAME]})
    with pytest.raises(TypeError):
        vault.protect("Jean Mbarga", {PERSON: ["Jean Mbarga", None]})
    assert len(vault) == 1
    assert vault.restore(token) == SECRET_NAME


@pytest.mark.parametrize("values", [(SECRET_NAME,), {SECRET_NAME}, frozenset({SECRET_NAME})])
def test_other_collections_of_str_are_accepted(values):
    vault = PseudonymVault()
    protected = vault.protect(SECRET_TEXT, {PERSON: values})
    assert shape(protected) == "[PERSONNE_1] [EMAIL]"


def test_a_generator_of_str_is_accepted():
    vault = PseudonymVault()
    protected = vault.protect(SECRET_TEXT, {PERSON: (name for name in [SECRET_NAME])})
    assert shape(protected) == "[PERSONNE_1] [EMAIL]"


def test_none_or_an_empty_mapping_means_no_known_value():
    assert PseudonymVault().protect(SECRET_TEXT) == f"{SECRET_NAME} [EMAIL]"
    assert PseudonymVault().protect(SECRET_TEXT, None) == f"{SECRET_NAME} [EMAIL]"
    assert PseudonymVault().protect(SECRET_TEXT, {}) == f"{SECRET_NAME} [EMAIL]"


@pytest.mark.parametrize("text", [None, b"Awa Ndiaye", 4111111111111111, ["Awa Ndiaye"]])
def test_protect_and_restore_require_a_str(text):
    message = _assert_refused(lambda vault: vault.protect(text))
    assert "text must be a str" in message
    message = _assert_refused(lambda vault: vault.restore(text))
    assert "text must be a str" in message


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("client_id_patterns", r"\bCLI-\d{6}\b"),
        ("client_id_patterns", None),
        ("client_id_patterns", [r"\bCLI-\d{6}\b", 42]),
        ("case_number_patterns", r"\bDOS/\d{4}\b"),
        ("case_number_patterns", [None]),
    ],
)
def test_identifier_patterns_must_be_a_collection_of_str(argument, value):
    with pytest.raises(TypeError) as error:
        PseudonymVault(**{argument: value})
    assert argument in str(error.value)


@pytest.mark.parametrize("detectors", [None, "detector", _FakeRecogniser(), [object()], [None]])
def test_plugged_detectors_must_be_a_collection_of_detectors(detectors):
    with pytest.raises(TypeError) as error:
        PseudonymVault(detectors=detectors)
    assert "detectors" in str(error.value)
