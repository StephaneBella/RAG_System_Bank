# SEC-02 T4 (clôture partielle) : contrôles amont sur la préparation des données RAG.
#
# Aucun prompt n'est construit ni envoyé à un fournisseur LLM dans le projet : ces tests
# vérifient uniquement l'étiquetage (N1), les métadonnées visibles par le LLM (N2) et le
# contenu des points Qdrant (N3). llama_index, qdrant_client et dotenv sont remplacés par
# des modules factices ; aucun serveur n'est contacté.

import importlib
import sys
import types
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

PACKAGE = "app.services.Agentic_RAG.Data_preparation"
CORPUS_DIR = Path(__file__).resolve().parents[2] / "data" / "documents"


@dataclass
class FakePointStruct:
    id: str
    vector: list[float]
    payload: dict


class FakeMetadataMode(Enum):
    ALL = "all"
    NONE = "none"


@pytest.fixture
def modules(monkeypatch):
    """Import ingestion and storing with stubbed third-party modules."""
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: False

    core = types.ModuleType("llama_index.core")
    core.SimpleDirectoryReader = MagicMock(name="SimpleDirectoryReader")
    schema = types.ModuleType("llama_index.core.schema")
    schema.Document = object
    schema.MetadataMode = FakeMetadataMode
    schema.TextNode = object

    qdrant = types.ModuleType("qdrant_client")
    qdrant.QdrantClient = MagicMock(name="QdrantClient")
    models = types.ModuleType("qdrant_client.models")
    models.PointStruct = FakePointStruct
    models.Distance = SimpleNamespace(COSINE="Cosine")
    models.PayloadSchemaType = SimpleNamespace(KEYWORD="keyword", INTEGER="integer")
    models.VectorParams = lambda **kwargs: kwargs

    stubs = {
        "dotenv": dotenv,
        "llama_index": types.ModuleType("llama_index"),
        "llama_index.core": core,
        "llama_index.core.schema": schema,
        "qdrant_client": qdrant,
        "qdrant_client.models": models,
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    for name in ("ingestion", "storing"):
        monkeypatch.delitem(sys.modules, f"{PACKAGE}.{name}", raising=False)

    ingestion = importlib.import_module(f"{PACKAGE}.ingestion")
    storing = importlib.import_module(f"{PACKAGE}.storing")
    monkeypatch.setattr(
        storing, "get_qdrant_client", MagicMock(side_effect=AssertionError("no real Qdrant"))
    )
    yield SimpleNamespace(ingestion=ingestion, storing=storing)
    for name in ("ingestion", "storing"):
        sys.modules.pop(f"{PACKAGE}.{name}", None)


def _raw_page(file_name, page_label="1"):
    """A page as returned by the PDF reader, before normalisation."""
    return SimpleNamespace(
        text=f"Contenu de {file_name}",
        metadata={
            "file_name": file_name,
            "file_path": f"/srv/ingestion/data/documents/{file_name}",
            "page_label": page_label,
        },
    )


def _allowed_labels(ingestion):
    doc_types = {ingestion.PUBLIC, ingestion.PRIVE}
    layers = {
        ingestion.FRONT_OFFICE,
        ingestion.CREDIT_RISQUES,
        ingestion.CONFORMITE,
        ingestion.DIRECTION_GENERALE,
    }
    return doc_types, layers


# --- N1 : étiquetage obligatoire --------------------------------------------------------


@pytest.mark.parametrize("file_name", ["Inconnu.pdf", "unknown", "prive__credit_risques.pdf"])
def test_n1_undeclared_pdf_is_refused(modules, file_name):
    page = _raw_page(file_name)
    if file_name == "unknown":
        del page.metadata["file_name"]  # the reader gave no file name at all
    with pytest.raises(ValueError, match="non déclaré"):
        modules.ingestion.normalize_metadata(page)


def test_n1_every_declared_document_has_valid_labels(modules):
    ingestion = modules.ingestion
    doc_types, layers = _allowed_labels(ingestion)
    assert ingestion.DOCUMENTS_METADATA
    for file_name, labels in ingestion.DOCUMENTS_METADATA.items():
        assert set(labels) == {"doc_type", "business_layer"}, file_name
        assert labels["doc_type"] in doc_types, file_name
        assert labels["business_layer"] in layers, file_name


def test_n1_registry_covers_the_whole_corpus(modules):
    corpus = {path.name for path in CORPUS_DIR.glob("*.pdf")}
    assert corpus, "no PDF found in data/documents"
    assert corpus == set(modules.ingestion.DOCUMENTS_METADATA)


def test_n1_normalisation_applies_the_registry_labels(modules):
    ingestion = modules.ingestion
    for file_name, labels in ingestion.DOCUMENTS_METADATA.items():
        page = ingestion.normalize_metadata(_raw_page(file_name, page_label="3"))
        assert page.metadata["doc_type"] == labels["doc_type"]
        assert page.metadata["business_layer"] == labels["business_layer"]
        assert page.metadata["source"] == file_name
        assert page.metadata["page"] == 3


# --- N2 : métadonnées visibles par le LLM ------------------------------------------------


def test_n2_file_path_is_excluded_from_llm_metadata(modules):
    ingestion = modules.ingestion
    for file_name in ingestion.DOCUMENTS_METADATA:
        page = ingestion.normalize_metadata(_raw_page(file_name))
        assert "file_path" in page.metadata  # kept for traceability...
        assert "file_path" in page.excluded_llm_metadata_keys  # ...but never shown to the LLM
        llm_visible = set(page.metadata) - set(page.excluded_llm_metadata_keys)
        assert llm_visible == {"source", "page", "doc_type", "business_layer"}


def test_n2_no_metadata_is_embedded_in_vectors(modules):
    page = modules.ingestion.normalize_metadata(_raw_page("Prive__Credit_Risques.pdf"))
    assert set(page.excluded_embed_metadata_keys) == set(page.metadata)


# --- N3 : contenu des points Qdrant ------------------------------------------------------


def _chunks_from_registry(ingestion):
    """Nodes carrying the metadata produced by normalize_metadata, as chunking keeps it."""
    nodes = []
    for index, file_name in enumerate(ingestion.DOCUMENTS_METADATA):
        page = ingestion.normalize_metadata(_raw_page(file_name))
        nodes.append(
            SimpleNamespace(
                node_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"chunk-{index}")),
                metadata={**page.metadata, "chunk_index": 0},
                get_content=lambda metadata_mode, text=page.text: text,
            )
        )
    return nodes


def _stored_payloads(modules):
    nodes = _chunks_from_registry(modules.ingestion)
    client = MagicMock()
    modules.storing.store_nodes(nodes, [[0.1, 0.2, 0.3]] * len(nodes), client=client)
    return [
        point.payload for call in client.upsert.call_args_list for point in call.kwargs["points"]
    ]


def test_n3_every_stored_point_carries_its_layer_labels(modules):
    payloads = _stored_payloads(modules)
    registry = modules.ingestion.DOCUMENTS_METADATA
    assert len(payloads) == len(registry)
    for payload in payloads:
        labels = registry[payload["source"]]
        assert payload["business_layer"] == labels["business_layer"]
        assert payload["doc_type"] == labels["doc_type"]
        assert payload["text"]


def test_n3_layer_labels_are_indexed_for_filtering(modules):
    client = MagicMock()
    modules.storing.ensure_collection(client, vector_size=3, collection="test")
    indexed = {call.args[1]: call.args[2] for call in client.create_payload_index.call_args_list}
    assert indexed["business_layer"] == "keyword"
    assert indexed["doc_type"] == "keyword"


def test_n3_known_finding_file_path_is_stored_in_qdrant(modules):
    # Known finding (documented in docs/SEC-02-matrice-des-droits.md, not fixed here):
    # the internal file path is excluded from the LLM view by llama_index, but it is still
    # stored in the Qdrant payload and would leak if a retriever forwarded payloads as is.
    for payload in _stored_payloads(modules):
        assert payload["file_path"].startswith("/srv/ingestion/data/documents/")
