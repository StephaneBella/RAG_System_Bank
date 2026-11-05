# US 07 T4 : minimisation du contexte RAG (module autonome, non branché).
#
# Extraits synthétiques uniquement. Les couches sont des chaînes opaques choisies par le
# test : le module ne connaît pas le registre d'ingestion. Seul le nom de la source C3
# est repris de la cartographie T1, et le test vérifie qu'il existe dans le corpus.

import math
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.services.pseudonymization import EntityType, PseudonymVault
from app.services.rag_context import (
    ContextStats,
    ExclusionReason,
    MinimizedContext,
    RetrievedChunk,
    build_context,
)

CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "documents"
C3_SOURCE = "Prive__Direction_Generale_Strategie.pdf"  # docs/US-07-cartographie, § 4.4.1
LAYER = "layer_a"
OTHER_LAYER = "layer_b"
SECRET = "Awa Ndiaye awa.test@example.org"
SOURCE_SIZE = len("doc_a.pdf")  # default source, counted in the context budget
TOKEN = re.compile(r"\[PERSONNE_\d+_[0-9a-f]{32}\]")


def chunk(text="Procédure de traitement.", *, source="doc_a.pdf", layer=LAYER, score=None, **meta):
    metadata = {"source": source, "business_layer": layer, **meta}
    return RetrievedChunk(text, {k: v for k, v in metadata.items() if v is not None}, score)


def build(chunks, **overrides):
    options = {
        "allowed_layers": [LAYER],
        "excluded_sources": [C3_SOURCE],
        "max_extracts": 10,
        "max_context_chars": 10_000,
    }
    options.update(overrides)
    return build_context(chunks, **options)


def texts(context: MinimizedContext) -> list[str]:
    return [extract.text for extract in context.extracts]


def excluded(context: MinimizedContext) -> dict[ExclusionReason, int]:
    return dict(context.stats.excluded)


# --- Cas normal ---------------------------------------------------------------------------


def test_allowed_extracts_are_kept_with_source_and_page_only():
    context = build(
        [
            chunk(
                "Seuil ≤ 10 M FCFA.",
                page=3,
                doc_type="prive",
                file_path="/srv/ingestion/data/documents/doc_a.pdf",
                chunk_index=0,
                id="0b6c1d2e",
            )
        ]
    )
    assert texts(context) == ["Seuil ≤ 10 M FCFA."]
    assert dict(context.extracts[0].metadata) == {"source": "doc_a.pdf", "page": 3}
    assert context.stats == ContextStats(1, 1, {}, len("Seuil ≤ 10 M FCFA.") + SOURCE_SIZE + 1)


def test_counters_always_add_up():
    chunks = [
        chunk("A"),
        chunk("A"),
        chunk("B", layer=OTHER_LAYER),
        chunk("C", source=C3_SOURCE),
        chunk("   "),
    ]
    context = build(chunks)
    stats = context.stats
    assert stats.received == len(chunks)
    assert stats.received == stats.retained + sum(stats.excluded.values())
    assert excluded(context) == {
        ExclusionReason.DUPLICATE: 1,
        ExclusionReason.LAYER_NOT_ALLOWED: 1,
        ExclusionReason.EXCLUDED_SOURCE: 1,
        ExclusionReason.EMPTY_TEXT: 1,
    }


def test_no_chunk_gives_an_empty_context():
    context = build([])
    assert context.extracts == ()
    assert context.stats == ContextStats(0, 0, {}, 0)


# --- Cloisonnement par couche (MIN-LLM-02) -----------------------------------------------


def test_extracts_of_another_layer_are_discarded():
    context = build([chunk("Couche A"), chunk("Couche B", layer=OTHER_LAYER)])
    assert texts(context) == ["Couche A"]
    assert excluded(context) == {ExclusionReason.LAYER_NOT_ALLOWED: 1}


@pytest.mark.parametrize("layer", [None, "", "   "])
def test_extracts_without_a_layer_are_discarded(layer):
    context = build([chunk(layer=layer)])
    assert context.extracts == ()
    assert excluded(context) == {ExclusionReason.MISSING_LAYER: 1}


def test_layers_are_opaque_exact_strings():
    chunks = [chunk("Exacte"), chunk("Casse", layer="LAYER_A"), chunk("Espace", layer=" layer_a")]
    assert texts(build(chunks)) == ["Exacte"]


def test_several_allowed_layers():
    chunks = [chunk("A"), chunk("B", layer=OTHER_LAYER), chunk("C", layer="layer_c")]
    assert texts(build(chunks, allowed_layers={LAYER, OTHER_LAYER})) == ["A", "B"]


# --- Sources exclues (MIN-DOC-04) ---------------------------------------------------------


def test_the_c3_source_name_comes_from_the_existing_corpus():
    assert (CORPUS_DIR / C3_SOURCE).is_file()


def test_excluded_sources_are_discarded_before_any_limit():
    chunks = [chunk("Note C3", source=C3_SOURCE, score=0.99), chunk("Procédure", score=0.1)]
    context = build(chunks, max_extracts=1, max_context_chars=len("Procédure") + SOURCE_SIZE)
    assert texts(context) == ["Procédure"]
    assert excluded(context) == {ExclusionReason.EXCLUDED_SOURCE: 1}


def test_an_excluded_source_is_discarded_whatever_its_layer():
    assert build([chunk(source=C3_SOURCE)], allowed_layers=[LAYER]).extracts == ()


def test_the_public_report_with_a_similar_name_is_not_excluded():
    public = "Public__Direction_Generale_Strategie_Rapport_annuel_COBAC.pdf"
    assert texts(build([chunk("Rapport public", source=public)])) == ["Rapport public"]


@pytest.mark.parametrize("source", [None, "", "  "])
def test_extracts_without_a_source_are_discarded(source):
    context = build([chunk(source=source)])
    assert context.extracts == ()
    assert excluded(context) == {ExclusionReason.MISSING_SOURCE: 1}


# --- Métadonnées (MIN-META-01 à 04) ------------------------------------------------------


def test_classification_is_only_sent_on_explicit_request():
    item = chunk(page=2, doc_type="prive", file_path="/srv/x.pdf", chunk_index=4)
    default = build([item]).extracts[0].metadata
    explicit = build([item], include_classification=True).extracts[0].metadata
    assert dict(default) == {"source": "doc_a.pdf", "page": 2}
    assert dict(explicit) == {
        "source": "doc_a.pdf",
        "page": 2,
        "doc_type": "prive",
        "business_layer": LAYER,
    }


def test_internal_metadata_are_never_sent():
    item = chunk(page="iv", file_path="/srv/x.pdf", chunk_index=1, id="p-1", node_id="n-1")
    for include_classification in (False, True):
        metadata = build([item], include_classification=include_classification).extracts[0].metadata
        assert not {"file_path", "chunk_index", "id", "node_id"} & set(metadata)
        assert metadata["page"] == "iv"


def test_a_missing_page_is_simply_absent():
    assert dict(build([chunk()]).extracts[0].metadata) == {"source": "doc_a.pdf"}


def test_transmitted_metadata_cannot_be_modified():
    metadata = build([chunk(page=1)]).extracts[0].metadata
    with pytest.raises(TypeError):
        metadata["file_path"] = "/srv/x.pdf"


# --- Doublons et tri ---------------------------------------------------------------------


def test_duplicates_are_removed_keeping_the_best_ranked():
    chunks = [
        chunk("Même texte", source="b.pdf", score=0.2),
        chunk("Même  texte\n", source="a.pdf", score=0.9),
    ]
    context = build(chunks)
    assert [extract.metadata["source"] for extract in context.extracts] == ["a.pdf"]
    assert excluded(context) == {ExclusionReason.DUPLICATE: 1}


def test_extracts_are_ranked_by_score_with_stable_ties():
    chunks = [
        chunk("C", score=0.1),
        chunk("A", score=0.9),
        chunk("B1", score=0.5),
        chunk("B2", score=0.5),
    ]
    assert texts(build(chunks)) == ["A", "B1", "B2", "C"]


def test_without_scores_the_retriever_order_is_kept():
    assert texts(build([chunk("Z"), chunk("A"), chunk("M")])) == ["Z", "A", "M"]


def test_integer_and_negative_scores_are_accepted():
    assert texts(build([chunk("Bas", score=-1), chunk("Haut", score=2)])) == ["Haut", "Bas"]


# --- Limites H6 : jamais de troncature ---------------------------------------------------


def test_max_extracts_is_applied_in_priority_order():
    chunks = [chunk(str(n), score=n) for n in range(5)]
    context = build(chunks, max_extracts=2)
    assert texts(context) == ["4", "3"]
    assert excluded(context) == {ExclusionReason.MAX_EXTRACTS: 3}


def test_an_extract_too_big_is_discarded_whole_and_the_next_ones_examined():
    chunks = [chunk("x" * 6, score=3), chunk("y" * 8, score=2), chunk("z" * 4, score=1)]
    context = build(chunks, max_context_chars=10 + 2 * SOURCE_SIZE)
    assert texts(context) == ["x" * 6, "z" * 4]
    assert excluded(context) == {ExclusionReason.CONTEXT_BUDGET: 1}
    assert context.stats.context_chars == 10 + 2 * SOURCE_SIZE


def test_an_extract_is_never_truncated():
    long_text = "Procédure complète. " * 50
    size = len(long_text) + SOURCE_SIZE
    context = build([chunk(long_text)], max_context_chars=size - 1)
    assert context.extracts == ()
    assert excluded(context) == {ExclusionReason.CONTEXT_BUDGET: 1}
    assert texts(build([chunk(long_text)], max_context_chars=size)) == [long_text]


# --- Pseudonymisation (T3) ---------------------------------------------------------------


def test_kept_texts_are_protected_by_the_vault_and_restorable():
    vault = PseudonymVault()
    context = build(
        [chunk(f"Dossier suivi par {SECRET}.")],
        vault=vault,
        known_values={EntityType.PERSON: ["Awa Ndiaye"]},
    )
    [text] = texts(context)
    assert "Awa" not in text and "example.org" not in text
    assert TOKEN.search(text) and "[EMAIL]" in text
    assert vault.restore(text) == "Dossier suivi par Awa Ndiaye [EMAIL]."


def test_the_budget_is_measured_on_the_protected_text():
    original = "Contact Awa"
    vault = PseudonymVault()
    context = build(
        [chunk(original)],
        vault=vault,
        known_values={EntityType.PERSON: ["Awa"]},
        max_context_chars=len(original) + SOURCE_SIZE + 5,
    )
    # The token is longer than the name: the protected extract no longer fits.
    assert context.extracts == ()
    assert excluded(context) == {ExclusionReason.CONTEXT_BUDGET: 1}


def test_out_of_scope_extracts_never_reach_the_vault():
    vault = PseudonymVault()
    build(
        [chunk(SECRET, layer=OTHER_LAYER), chunk(SECRET, source=C3_SOURCE)],
        vault=vault,
        known_values={EntityType.PERSON: ["Awa Ndiaye"]},
    )
    assert len(vault) == 0


def test_without_a_vault_texts_are_kept_exactly():
    text = "Réf. CONF-LBC-001 :\u00a0seuil ≤ 10 M FCFA."
    assert texts(build([chunk(text)])) == [text]


# --- Entrées invalides : refus explicite avant tout traitement ---------------------------


@pytest.mark.parametrize("chunks", [None, "texte", b"texte", {"text": "x"}, 3, chunk()])
def test_chunks_must_be_a_collection(chunks):
    with pytest.raises(TypeError, match="chunks must be a collection"):
        build(chunks)


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"text": SECRET, "metadata": {}}, "only contain RetrievedChunk"),
        (RetrievedChunk(None, {}), "text must be a str"),
        (RetrievedChunk(b"x", {}), "text must be a str"),
        (RetrievedChunk(SECRET, None), "metadata must be a mapping"),
        (RetrievedChunk(SECRET, [("source", "a.pdf")]), "metadata must be a mapping"),
        (RetrievedChunk(SECRET, {1: "a.pdf"}), "keys must be str"),
        (RetrievedChunk(SECRET, {"source": 1}), "source must be a str"),
        (RetrievedChunk(SECRET, {"business_layer": ["layer_a"]}), "business_layer must be a str"),
        (RetrievedChunk(SECRET, {"doc_type": 0}), "doc_type must be a str"),
        (RetrievedChunk(SECRET, {"page": True}), "page must be an int or a str"),
        (RetrievedChunk(SECRET, {"page": 1.5}), "page must be an int or a str"),
        (RetrievedChunk(SECRET, {}, "0.5"), "score must be a number"),
        (RetrievedChunk(SECRET, {}, True), "score must be a number"),
        (RetrievedChunk(SECRET, {}, math.nan), "score must be finite"),
        (RetrievedChunk(SECRET, {}, math.inf), "score must be finite"),
    ],
)
def test_malformed_chunks_are_refused_without_exposing_content(item, message):
    with pytest.raises((TypeError, ValueError), match=message) as error:
        build([chunk(), item])
    assert "Awa" not in str(error.value) and "example.org" not in str(error.value)


def test_scores_must_be_given_for_all_chunks_or_none():
    with pytest.raises(ValueError, match="every chunk or for none"):
        build([chunk("A", score=0.5), chunk("B")])


@pytest.mark.parametrize(
    ("argument", "value", "error"),
    [
        ("allowed_layers", LAYER, TypeError),
        ("allowed_layers", None, TypeError),
        ("allowed_layers", [], ValueError),
        ("allowed_layers", [LAYER, None], TypeError),
        ("allowed_layers", [LAYER, " "], ValueError),
        ("excluded_sources", C3_SOURCE, TypeError),
        ("excluded_sources", None, TypeError),
        ("excluded_sources", [], ValueError),
        ("excluded_sources", set(), ValueError),
        ("excluded_sources", [C3_SOURCE, 3], TypeError),
        ("excluded_sources", [""], ValueError),
        ("max_extracts", None, TypeError),
        ("max_extracts", 0, ValueError),
        ("max_extracts", -1, ValueError),
        ("max_extracts", True, TypeError),
        ("max_extracts", 2.0, TypeError),
        ("max_extracts", "3", TypeError),
        ("max_context_chars", None, TypeError),
        ("max_context_chars", 0, ValueError),
        ("max_context_chars", False, TypeError),
        ("max_context_chars", 1e3, TypeError),
        ("include_classification", 1, TypeError),
        ("include_classification", "yes", TypeError),
        ("include_classification", None, TypeError),
        ("vault", object(), TypeError),
        ("vault", "vault", TypeError),
    ],
)
def test_invalid_options_are_refused(argument, value, error):
    with pytest.raises(error, match=argument):
        build([chunk()], **{argument: value})


@pytest.mark.parametrize(
    "argument", ["allowed_layers", "excluded_sources", "max_extracts", "max_context_chars"]
)
def test_limits_and_scopes_have_no_default(argument):
    options = {
        "allowed_layers": [LAYER],
        "excluded_sources": [C3_SOURCE],
        "max_extracts": 1,
        "max_context_chars": 100,
    }
    del options[argument]
    with pytest.raises(TypeError, match=argument):
        build_context([chunk()], **options)


def test_known_values_require_a_vault():
    with pytest.raises(ValueError, match="requires a vault"):
        build([chunk(SECRET)], known_values={EntityType.PERSON: ["Awa Ndiaye"]})


@pytest.mark.parametrize(
    "known_values",
    [
        "Awa Ndiaye",
        {EntityType.PERSON: "Awa Ndiaye"},
        {EntityType.PERSON: [None]},
        {"PERSONNE": []},
    ],
)
def test_invalid_known_values_are_refused_before_any_processing(known_values):
    vault = PseudonymVault()
    with pytest.raises(TypeError) as error:
        build([chunk(SECRET)], vault=vault, known_values=known_values)
    assert len(vault) == 0
    assert "Awa" not in str(error.value)


def test_one_invalid_chunk_stops_everything_before_the_vault_is_used():
    vault = PseudonymVault()
    with pytest.raises(TypeError):
        build(
            [chunk(SECRET), RetrievedChunk(SECRET, {"source": 1})],
            vault=vault,
            known_values={EntityType.PERSON: ["Awa Ndiaye"]},
        )
    assert len(vault) == 0


# --- Aucune exposition de contenu --------------------------------------------------------


def test_reprs_and_counters_hold_no_content():
    item = chunk(SECRET, page=1, file_path="/srv/secret/path.pdf")
    context = build([item, chunk(SECRET, layer=OTHER_LAYER)])
    exposed = " ".join([repr(item), repr(context), str(context), repr(context.stats)])
    for value in ("Awa", "example.org", "/srv/secret", "doc_a.pdf"):
        assert value not in exposed


# --- F1 : valeurs connues lues une seule fois ---------------------------------------------

PERSON = EntityType.PERSON
NAMED = "Dossier de Awa Ndiaye."


def _person_values(kind):
    names = ["Awa Ndiaye"]
    return {"generator": (name for name in names), "iterator": iter(names)}[kind]


@pytest.mark.parametrize("kind", ["generator", "iterator"])
@pytest.mark.parametrize("count", [1, 3])
def test_one_shot_known_values_protect_every_extract(kind, count):
    vault = PseudonymVault()
    chunks = [chunk(f"{NAMED} Pièce {n}.") for n in range(count)]
    context = build(chunks, vault=vault, known_values={PERSON: _person_values(kind)})
    assert len(context.extracts) == count
    for text in texts(context):
        assert "Awa" not in text
        assert TOKEN.search(text)
    assert len(vault) == 1  # the same token for the same name in every extract


def test_known_values_are_read_only_once():
    reads = []

    def names():
        reads.append(1)
        yield "Awa Ndiaye"

    build(
        [chunk(NAMED), chunk(f"Encore {NAMED}")],
        vault=PseudonymVault(),
        known_values={PERSON: names()},
    )
    assert reads == [1]


def test_a_known_values_mapping_is_read_only_once():
    class Shifting(Mapping):
        """Gives the name on the first read only, then nothing."""

        def __init__(self):
            self.reads = 0

        def __getitem__(self, key):
            self.reads += 1
            return ["Awa Ndiaye"] if self.reads == 1 else []

        def __iter__(self):
            return iter([PERSON])

        def __len__(self):
            return 1

    context = build(
        [chunk(NAMED), chunk(f"Encore {NAMED}")], vault=PseudonymVault(), known_values=Shifting()
    )
    assert all("Awa" not in text for text in texts(context))


@pytest.mark.parametrize(
    "known_values",
    [
        {PERSON: "Awa Ndiaye"},
        {PERSON: None},
        {PERSON: (name for name in ["Awa Ndiaye", None])},
        {PERSON: iter([b"Awa Ndiaye"])},
        {"PERSONNE": (name for name in ["Awa Ndiaye"])},
    ],
)
def test_frozen_known_values_keep_t3_refusals(known_values):
    vault = PseudonymVault()
    with pytest.raises(TypeError) as error:
        build([chunk(NAMED)], vault=vault, known_values=known_values)
    assert len(vault) == 0
    assert "Awa" not in str(error.value)


# --- F2 : limite figée, obligation de T5 --------------------------------------------------


def test_known_limit_a_discarded_extract_stays_in_the_vault():
    # The second extract is pseudonymised before the size check, then discarded: its
    # value is in the vault although it is never transmitted. T5 must only restore the
    # tokens present in the prompt it actually sends (see the module docstring).
    vault = PseudonymVault()
    kept = "Voir Awa Ndiaye."
    context = build(
        [chunk(kept, score=2), chunk("Dossier de Jean Mbarga. " + "x" * 200, score=1)],
        vault=vault,
        known_values={PERSON: ["Awa Ndiaye", "Jean Mbarga"]},
        max_context_chars=150,
    )
    [sent] = texts(context)
    assert excluded(context) == {ExclusionReason.CONTEXT_BUDGET: 1}
    assert "Jean" not in sent and len(vault) == 2
    guessed = TOKEN.search(sent).group().replace("PERSONNE_1_", "PERSONNE_2_")
    assert guessed not in sent
    assert vault.restore(guessed) == "Jean Mbarga"


def test_duplicates_and_extracts_over_the_limit_never_reach_the_vault():
    vault = PseudonymVault()
    build(
        [
            chunk("Awa Ndiaye", score=3),
            chunk(" Awa  Ndiaye ", score=2),
            chunk("Jean Mbarga", score=1),
        ],
        vault=vault,
        known_values={PERSON: ["Awa Ndiaye", "Jean Mbarga"]},
        max_extracts=1,
    )
    assert len(vault) == 1


# --- F3 : métadonnées pseudonymisées, comptées, sans chemin -------------------------------


def test_with_a_vault_source_and_textual_page_are_protected():
    vault = PseudonymVault()
    context = build(
        [chunk("Texte.", source="Dossier_Awa_Ndiaye.pdf", page="Awa Ndiaye", doc_type="prive")],
        vault=vault,
        known_values={PERSON: ["Awa Ndiaye", "Dossier_Awa_Ndiaye.pdf"]},
        include_classification=True,
    )
    metadata = dict(context.extracts[0].metadata)
    assert "Awa" not in metadata["source"] and "Awa" not in metadata["page"]
    assert vault.restore(metadata["source"]) == "Dossier_Awa_Ndiaye.pdf"
    assert vault.restore(metadata["page"]) == "Awa Ndiaye"
    assert (metadata["doc_type"], metadata["business_layer"]) == ("prive", LAYER)


def test_an_integer_page_keeps_its_type_with_a_vault():
    context = build([chunk(page=12)], vault=PseudonymVault())
    assert context.extracts[0].metadata["page"] == 12


def test_metadata_are_counted_in_the_budget():
    item = chunk("abc", source="s.pdf", page=123, doc_type="prive")
    exact = len("abc") + len("s.pdf") + len("123")
    assert texts(build([item], max_context_chars=exact)) == ["abc"]
    assert build([item], max_context_chars=exact - 1).extracts == ()
    with_classification = exact + len("prive") + len(LAYER)
    assert build([item], max_context_chars=exact, include_classification=True).extracts == ()
    context = build([item], max_context_chars=with_classification, include_classification=True)
    assert context.stats.context_chars == with_classification


def test_a_huge_page_label_cannot_bypass_the_budget():
    context = build([chunk("abc", page="y" * 1000)], max_context_chars=100)
    assert context.extracts == ()
    assert excluded(context) == {ExclusionReason.CONTEXT_BUDGET: 1}


def test_the_budget_counts_protected_metadata():
    vault = PseudonymVault()
    context = build(
        [chunk("abc", source="Awa.pdf")],
        vault=vault,
        known_values={PERSON: ["Awa"]},
        max_context_chars=len("abc") + len("Awa.pdf") + 5,
    )
    assert context.extracts == ()


@pytest.mark.parametrize(
    "source", ["/srv/ingestion/data/documents/a.pdf", "dir/a.pdf", "C:\\docs\\Awa Ndiaye.pdf"]
)
def test_a_source_with_a_path_separator_is_refused(source):
    with pytest.raises(ValueError, match="path separator") as error:
        build([chunk(), chunk(source=source)])
    assert "Awa" not in str(error.value) and "srv" not in str(error.value)


def test_internal_metadata_are_never_sent_even_with_a_vault():
    item = chunk(page=1, file_path="/srv/x.pdf", chunk_index=1, id="p-1", doc_type="prive")
    for include_classification in (False, True):
        metadata = (
            build([item], vault=PseudonymVault(), include_classification=include_classification)
            .extracts[0]
            .metadata
        )
        assert not {"file_path", "chunk_index", "id"} & set(metadata)


# --- F4 : chaînes exactes -----------------------------------------------------------------


class ShortStr(str):
    """A str subclass that lies about its length."""

    def __len__(self):
        return 0


def test_a_str_subclass_cannot_hide_the_text_size():
    with pytest.raises(TypeError, match="text must be a str"):
        build([chunk(ShortStr("z" * 5000))], max_context_chars=10)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"source": ShortStr("doc_a.pdf")}, "source must be a str"),
        ({"layer": ShortStr(LAYER)}, "business_layer must be a str"),
        ({"page": ShortStr("y" * 1000)}, "page must be an int or a str"),
        ({"doc_type": ShortStr("prive")}, "doc_type must be a str"),
    ],
)
def test_metadata_must_be_exact_str(overrides, message):
    with pytest.raises(TypeError, match=message):
        build([chunk(**overrides)])


def test_scopes_must_contain_exact_str():
    with pytest.raises(TypeError, match="allowed_layers"):
        build([chunk()], allowed_layers=[ShortStr(LAYER)])
    with pytest.raises(TypeError, match="excluded_sources"):
        build([chunk()], excluded_sources=[ShortStr(C3_SOURCE)])


def test_number_subclasses_are_refused():
    class Score(float):
        def __neg__(self):
            return 0.0

    class Limit(int):
        pass

    with pytest.raises(TypeError, match="score must be a number"):
        build([chunk(score=Score(1.0))])
    with pytest.raises(TypeError, match="max_extracts"):
        build([chunk()], max_extracts=Limit(1))


# --- F5 : une seule lecture des métadonnées -----------------------------------------------


class ShiftingMetadata(Mapping):
    """Returns an allowed source on its first read and the C3 source afterwards."""

    def __init__(self):
        self.reads = {}

    def __getitem__(self, key):
        self.reads[key] = self.reads.get(key, 0) + 1
        if key == "business_layer":
            return LAYER
        if key == "source":
            return "doc_a.pdf" if self.reads[key] == 1 else C3_SOURCE
        raise KeyError(key)

    def __iter__(self):
        return iter(["source", "business_layer"])

    def __len__(self):
        return 2


def test_metadata_are_read_once_and_checked_on_that_copy():
    metadata = ShiftingMetadata()
    context = build([RetrievedChunk("Texte.", metadata)])
    assert [dict(extract.metadata) for extract in context.extracts] == [{"source": "doc_a.pdf"}]
    assert metadata.reads == {"source": 1, "business_layer": 1}


def test_a_changing_mapping_cannot_smuggle_an_excluded_source():
    for _ in range(3):
        context = build([RetrievedChunk("Texte.", ShiftingMetadata())])
        assert all(extract.metadata["source"] != C3_SOURCE for extract in context.extracts)
