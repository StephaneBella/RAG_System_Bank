# Tests de l'écriture par lots dans Qdrant (store_nodes)
#
# llama_index et qdrant_client ne font pas partie des dépendances du backend :
# ils sont remplacés par des stubs le temps du test, et aucun serveur n'est contacté.

import importlib
import logging
import sys
import types
import uuid
from dataclasses import dataclass
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

MODULE = "app.services.Agentic_RAG.Data_preparation.storing"


@dataclass
class FakePointStruct:
    id: str
    vector: list[float]
    payload: dict


class FakeMetadataMode(Enum):
    ALL = "all"
    NONE = "none"


@pytest.fixture
def storing(monkeypatch):
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: False

    schema = types.ModuleType("llama_index.core.schema")
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
        "llama_index.core": types.ModuleType("llama_index.core"),
        "llama_index.core.schema": schema,
        "qdrant_client": qdrant,
        "qdrant_client.models": models,
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)

    module = importlib.import_module(MODULE)
    monkeypatch.setattr(
        module, "get_qdrant_client", MagicMock(side_effect=AssertionError("no real Qdrant"))
    )
    yield module
    sys.modules.pop(MODULE, None)


def _nodes(count):
    def node(i):
        return SimpleNamespace(
            node_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"node-{i}")),
            metadata={"source": "doc.pdf", "page": i // 10, "business_layer": "front_office"},
            get_content=lambda metadata_mode, i=i: f"chunk {i} ({metadata_mode.value})",
        )

    return [node(i) for i in range(count)], [[float(i), 0.5, 1.0] for i in range(count)]


def _upserted(client):
    return [call.kwargs for call in client.upsert.call_args_list]


@pytest.mark.parametrize(
    ("count", "batch_sizes"),
    [
        (1, [1]),
        (64, [64]),
        (65, [64, 1]),
        (130, [64, 64, 2]),
    ],
)
def test_every_batch_is_sent_once_and_total_is_returned(storing, caplog, count, batch_sizes):
    caplog.set_level(logging.INFO, logger=MODULE)
    nodes, vectors = _nodes(count)
    client = MagicMock()

    assert storing.store_nodes(nodes, vectors, client=client, collection="test") == count

    calls = _upserted(client)
    assert [len(call["points"]) for call in calls] == batch_sizes
    assert all(call["collection_name"] == "test" and call["wait"] is True for call in calls)

    sent_ids = [point.id for call in calls for point in call["points"]]
    assert sent_ids == [node.node_id for node in nodes]
    assert len(set(sent_ids)) == count

    logged = [r.getMessage() for r in caplog.records if r.name == MODULE]
    assert logged == [f"{size} point(s) écrits dans 'test'" for size in batch_sizes]


def test_zero_points_contacts_nothing(storing, caplog):
    caplog.set_level(logging.INFO, logger=MODULE)
    client = MagicMock()

    assert storing.store_nodes([], [], client=client) == 0
    assert storing.store_nodes([], []) == 0

    assert client.method_calls == []
    storing.get_qdrant_client.assert_not_called()
    assert [r for r in caplog.records if r.name == MODULE] == []


def test_points_carry_vector_text_and_metadata(storing):
    nodes, vectors = _nodes(2)
    client = MagicMock()

    storing.store_nodes(nodes, vectors, client=client, collection="test")

    first = _upserted(client)[0]["points"][0]
    assert first.vector == [0.0, 0.5, 1.0]
    assert first.payload == {
        "text": "chunk 0 (none)",
        "source": "doc.pdf",
        "page": 0,
        "business_layer": "front_office",
    }


def test_collection_is_prepared_once_with_vector_size(storing):
    nodes, vectors = _nodes(130)
    client = MagicMock()
    client.collection_exists.return_value = False

    storing.store_nodes(nodes, vectors, client=client, collection="test")

    client.collection_exists.assert_called_once_with("test")
    client.create_collection.assert_called_once()
    assert client.create_collection.call_args.kwargs["vectors_config"]["size"] == 3
    assert client.create_payload_index.call_count == 4


def test_custom_batch_size(storing):
    nodes, vectors = _nodes(25)
    client = MagicMock()

    assert storing.store_nodes(nodes, vectors, client=client, batch_size=10) == 25
    assert [len(call["points"]) for call in _upserted(client)] == [10, 10, 5]


def test_default_client_is_used_when_none_is_given(storing):
    client = MagicMock()
    storing.get_qdrant_client = MagicMock(return_value=client)
    nodes, vectors = _nodes(65)

    assert storing.store_nodes(nodes, vectors) == 65
    storing.get_qdrant_client.assert_called_once_with()
    assert client.upsert.call_count == 2


def test_length_mismatch_is_rejected_before_any_call(storing):
    nodes, vectors = _nodes(3)
    client = MagicMock()

    with pytest.raises(ValueError):
        storing.store_nodes(nodes, vectors[:2], client=client)
    assert client.method_calls == []
