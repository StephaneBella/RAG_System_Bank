"""
Etape 2 : chunking sémantique avec LlamaIndex.

Le SemanticSplitterNodeParser est appliqué oage par page (chaque Document = 
une page) : un chunk ne chevauche donc jamais deux pages et conserve les métadonnées 'source' et 'page'.
"""

from __future__ import annotations

import uuid

from llama_index.core.node_parser import SemanticSplitterNodeParser
from llama_index.core.schema import Document, MetadataMode, TextNode

from vectorisation import get_embed_model

def build_splitter(
        buffer_size: int = 1,
        breakpoint_percentile_threshold: int = 95,
) -> SemanticSplitterNodeParser:
    """
    buffer_size : nb de phrases regroupées pour comparer la similarité. 
    breakpoint_percentile_threshold : plus il est BAS, plus il y a de coupures
    (chunks plus petits) ; plus il est HAUT, plus les chunks sont gros.
    """
    return SemanticSplitterNodeParser(
        buffer_size=buffer_size,
        breakpoint_percentile_threshold=breakpoint_percentile_threshold,
        embed_model=get_embed_model()
    )


def chunk_documents(documents: list[Document], **splitter_kwargs) -> list[TextNode]:
    splitter = build_splitter(**splitter_kwargs)
    nodes = splitter.get_nodes_from_documents(documents, show_progress=True)

    per_page_counter : dict[tuple, int] = {}
    for node in nodes:
        meta = node.metadata
        key = (meta.get("source"), meta.get("page"))
        idx = per_page_counter.get(key, 0)
        per_page_counter[key] = idx + 1

        meta["chunk_index"] = idx # position du chunk dans la page
        # ID déterministe -> ré-ingestion idempotente (upsert Qdrant)
        text = node.get_content(metadata_mode=MetadataMode.NONE)
        node.id_ = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key} | {idx} | {text}"))

    return [n for n in nodes if n.get_content(metadata_mode=MetadataMode.NONE).strip()]
