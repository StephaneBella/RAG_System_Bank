import os
from functools import lru_cache

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


@lru_cache(maxsize=1)
def get_embed_model() -> HuggingFaceEmbedding:
    """Singleton : le modèle n'est chargé qu'une fois."""
    return HuggingFaceEmbedding(model_name=EMBEDDING_MODEL, normalize=True)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embed_model().get_text_embedding_batch(texts, show_progress=True)


def embed_query(query: str) -> list[float]:
    return get_embed_model().get_query_embedding(query)
