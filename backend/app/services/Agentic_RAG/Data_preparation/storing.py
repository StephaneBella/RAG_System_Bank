from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from llama_index.core.schema import MetadataMode, TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

load_dotenv()
logger = logging.getLogger(__name__)

COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "rag_system_bank")


def get_qdrant_client() -> QdrantClient:
    url, api_key = os.getenv("QDRANT_URL"), os.getenv("QDRANT_API_KEY")
    if not url or not api_key:
        raise RuntimeError("QDRANT_URL et QDRANT_API_KEY doivent etre définis (.env)")
    return QdrantClient(url=url, api_key=api_key)


def ensure_collection(client: QdrantClient, vector_size: int, collection: str = COLLECTION_NAME) -> None:
    """Crée la collection si besoin, puis garantit les index de payload.
    Les index sur doc_type / business_layer servent au filtrage RBAC coté Qdrant.
    """
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info("Collection '%s' créée (dim=%d)", collection, vector_size)

    for field, schema in [
        ("source", PayloadSchemaType.KEYWORD),
        ("doc_type", PayloadSchemaType.KEYWORD),
        ("business_layer", PayloadSchemaType.KEYWORD),
        ("page", PayloadSchemaType.INTEGER),
        # Clés imbriquées utilisées par le self-query retriever (LangChain)
        ("metadata.source", PayloadSchemaType.KEYWORD),
        ("metadata.doc_type", PayloadSchemaType.KEYWORD),
        ("metadata.business_layer", PayloadSchemaType.KEYWORD),
        ("metadata.page", PayloadSchemaType.INTEGER),
    ]:
        client.create_payload_index(collection, field, schema) #idempotent
    


def store_nodes(
        nodes: list[TextNode],
        vectors: list[list[float]],
        client: QdrantClient | None = None,
        collection: str = COLLECTION_NAME,
        batch_size: int = 64,
) -> int:
    """Upsert par lots. Retourne le nombre de points écrits."""
    if len(nodes) != len(vectors):
        raise ValueError("nodes et vectors doivent avoir la meme longueur")
    if not nodes:
        return 0

    client = client or get_qdrant_client()
    ensure_collection(client, vector_size=len(vectors[0]), collection=collection)

    points = [
        PointStruct(
            id=n.node_id,
            vector=v,
            payload={
                "text": n.get_content(metadata_mode=MetadataMode.NONE),
                "metadata": dict(n.metadata),  # copie imbriquée : lue par LangChain / self-query
                **n.metadata,       # copie à plat : lue par LlamaIndex / filtres RBAC
            },
            
        )
        for n, v in zip(nodes, vectors)
    ]
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=collection, points=points[i : i + batch_size], wait=True)
        logger.info("%d point(s) écrits dans '%s'", len(points), collection)
    return len(points)