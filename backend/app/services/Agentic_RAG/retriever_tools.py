"""Retrievers (LlamaIndex + LangChain), RBAC et audit.

Stratégies exposées via `retrieve(strategy, query, user)` :
  - vector_mmr  : LlamaIndex VectorIndexRetriever + MMR
  - multi_query : LangChain MultiQueryRetriever (base = Qdrant MMR)
  - self_query  : LangChain SelfQueryRetriever (filtres de métadonnées déduits par le LLM)
  - bm25        : LlamaIndex BM25Retriever (lexical)
  - fusion      : LlamaIndex QueryFusionRetriever (MMR vectoriel + BM25, RRF)

Sécurité : le filtre d'accès (RBAC) est appliqué DANS la requête Qdrant (et à la
construction de l'index BM25), puis revérifié sur les résultats (défense en profondeur).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import Stemmer
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.embeddings import Embeddings
from langchain_core.prompts import PromptTemplate
from langchain_qdrant import QdrantVectorStore as LCQdrantVectorStore
from llama_index.core import VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever as LIRetriever
from llama_index.core.retrievers import QueryFusionRetriever, VectorIndexRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle, TextNode
from llama_index.llms.anthropic import Anthropic as LlamaAnthropic
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.qdrant import QdrantVectorStore as LIQdrantVectorStore
from qdrant_client import models as qm

try:  # LangChain >= 1.0
    from langchain_classic.chains.query_constructor.schema import AttributeInfo
    from langchain_classic.retrievers.multi_query import MultiQueryRetriever
    from langchain_classic.retrievers.self_query.base import SelfQueryRetriever
except ImportError:  # LangChain < 1.0
    from langchain.chains.query_constructor.schema import AttributeInfo
    from langchain.retrievers.multi_query import MultiQueryRetriever
    from langchain.retrievers.self_query.base import SelfQueryRetriever

from Data_preparation.ingestion import (
    CONFORMITE, CREDIT_RISQUES, DIRECTION_GENERALE, FRONT_OFFICE, PRIVE, PUBLIC,
)
from Data_preparation.storing import COLLECTION_NAME, get_qdrant_client
from Data_preparation.vectorisation import get_embed_model

load_dotenv()
logger = logging.getLogger(__name__)
audit = logging.getLogger("rag.audit")

LLM_MODEL = os.getenv("LLM_MODEL", "groq:openai-gpt-oss-120b")


# =====================================================================
# 1. RBAC  (À ADAPTER : politique provisoire, à valider avec vos règles)
# =====================================================================
ALL = "*"
ALL_LAYERS = frozenset({FRONT_OFFICE, CREDIT_RISQUES, CONFORMITE, DIRECTION_GENERALE})

# rôle -> {type de document: couches accessibles}
ROLE_POLICIES: dict[str, dict[str, Any]] = {
    "charge_clientele":   {PUBLIC: ALL, PRIVE: {FRONT_OFFICE}},
    "credit_risques":     {PUBLIC: ALL, PRIVE: {CREDIT_RISQUES}},
    "conformite":         {PUBLIC: ALL, PRIVE: {CONFORMITE}},
    "direction_generale": {PUBLIC: ALL, PRIVE: ALL},
}


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: str


Scope = dict[str, frozenset]  # doc_type -> couches autorisées


def resolve_scope(user: UserContext) -> Scope:
    """Deny by default : rôle inconnu => PermissionError."""
    policy = ROLE_POLICIES.get(user.role)
    if not policy:
        raise PermissionError(f"Rôle inconnu ou sans droits : {user.role!r}")
    scope = {dt: (ALL_LAYERS if layers == ALL else frozenset(layers)) for dt, layers in policy.items()}
    return {dt: l for dt, l in scope.items() if l}


def build_access_filter(scope: Scope) -> qm.Filter:
    """Filtre Qdrant : OR de (doc_type = X AND business_layer IN couches)."""
    clauses = [
        qm.Filter(must=[
            qm.FieldCondition(key="doc_type", match=qm.MatchValue(value=dt)),
            qm.FieldCondition(key="business_layer", match=qm.MatchAny(any=sorted(layers))),
        ])
        for dt, layers in scope.items()
    ]
    if not clauses:
        raise PermissionError("Périmètre d'accès vide")
    return qm.Filter(should=clauses)


def _scope_key(scope: Scope) -> tuple:
    return tuple(sorted((dt, tuple(sorted(l))) for dt, l in scope.items()))


def log_event(event: str, user: UserContext, **fields: Any) -> None:
    """Journal d'audit unique (JSON) : recherches, réponses, accès mémoire."""
    audit.info(json.dumps({"event": event, "user_id": user.user_id, "role": user.role, **fields},
                          ensure_ascii=False))


# =====================================================================
# 2. Résultat normalisé
# =====================================================================
@dataclass
class RetrievedChunk:
    text: str
    source: str
    page: int | str | None
    doc_type: str | None
    business_layer: str | None
    score: float | None
    retriever: str
    chunk_id: str | None = None


def _from_llama(n: NodeWithScore, retriever: str) -> RetrievedChunk:
    m = n.node.metadata
    return RetrievedChunk(
        text=n.node.get_content(metadata_mode=MetadataMode.NONE),
        source=m.get("source", "inconnu"), page=m.get("page"),
        doc_type=m.get("doc_type"), business_layer=m.get("business_layer"),
        score=n.score, retriever=retriever, chunk_id=n.node.node_id,
    )


def _from_langchain(doc, retriever: str) -> RetrievedChunk:
    m = doc.metadata or {}
    return RetrievedChunk(
        text=doc.page_content,
        source=m.get("source", "inconnu"), page=m.get("page"),
        doc_type=m.get("doc_type"), business_layer=m.get("business_layer"),
        score=None, retriever=retriever, chunk_id=str(getattr(doc, "id", None) or ""),
    )


def enforce_access(chunks: list[RetrievedChunk], scope: Scope) -> list[RetrievedChunk]:
    """Dernier garde-fou : élimine tout chunk hors périmètre avant le LLM."""
    safe = [c for c in chunks if c.business_layer in scope.get(c.doc_type, ())]
    if len(safe) != len(chunks):
        logger.warning("%d chunk(s) hors périmètre éliminés", len(chunks) - len(safe))
    return safe


# =====================================================================
# 3. Ressources partagées
# =====================================================================
@lru_cache(maxsize=1)
def _client():
    return get_qdrant_client()


@lru_cache(maxsize=1)
def _llama_llm() -> LlamaAnthropic:
    return LlamaAnthropic(model=LLM_MODEL, temperature=0.0, max_tokens=1024)


@lru_cache(maxsize=1)
def _lc_llm() -> ChatGroq:
    return ChatGroq(model=LLM_MODEL, temperature=0, max_tokens=1024)


class LlamaIndexEmbeddings(Embeddings):
    """Adaptateur : LangChain réutilise le MÊME modèle d'embedding que LlamaIndex."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return get_embed_model().get_text_embedding_batch(texts)

    def embed_query(self, text: str) -> list[float]:
        return get_embed_model().get_query_embedding(text)


# =====================================================================
# 4. Retrievers LlamaIndex
# =====================================================================
@lru_cache(maxsize=1)
def _llama_index() -> VectorStoreIndex:
    vs = LIQdrantVectorStore(client=_client(), collection_name=COLLECTION_NAME)
    return VectorStoreIndex.from_vector_store(vs, embed_model=get_embed_model())


class MMRVectorRetriever(LIRetriever):
    """VectorIndexRetriever + MMR.

    Le vector store Qdrant de LlamaIndex n'implémente pas le mode MMR (le mode
    "mmr" serait ignoré en silence) : on récupère `fetch_k` candidats filtrés par
    RBAC, puis on applique le MMR nous-mêmes.
    lambda_mult : 1.0 = pertinence pure, 0.0 = diversité maximale.
    """

    def __init__(self, access_filter: qm.Filter, top_k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.6):
        super().__init__()
        self._top_k, self._lambda = top_k, lambda_mult
        self._base = VectorIndexRetriever(
            index=_llama_index(),
            similarity_top_k=fetch_k,
            vector_store_kwargs={"qdrant_filters": access_filter},
        )

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        candidates = self._base.retrieve(query_bundle)
        if len(candidates) <= 1:
            return candidates

        model = get_embed_model()  # embeddings normalisés => produit scalaire = cosinus
        q = np.array(model.get_query_embedding(query_bundle.query_str))
        d = np.array(model.get_text_embedding_batch([c.node.get_content() for c in candidates]))
        sim_q, sim_dd = d @ q, d @ d.T

        selected = [int(np.argmax(sim_q))]
        remaining = set(range(len(candidates))) - set(selected)
        while remaining and len(selected) < self._top_k:
            best = max(
                remaining,
                key=lambda i: self._lambda * sim_q[i] - (1 - self._lambda) * max(sim_dd[i][j] for j in selected),
            )
            selected.append(best)
            remaining.remove(best)
        return [NodeWithScore(node=candidates[i].node, score=float(sim_q[i])) for i in selected]


_bm25_cache: dict[tuple, BM25Retriever] = {}


def _load_scope_nodes(scope: Scope) -> list[TextNode]:
    """Charge depuis Qdrant les seuls chunks accessibles (le BM25 vit en mémoire)."""
    flt, nodes, offset = build_access_filter(scope), [], None
    while True:
        points, offset = _client().scroll(
            COLLECTION_NAME, scroll_filter=flt, limit=256,
            with_payload=True, with_vectors=False, offset=offset,
        )
        for p in points:
            pl = p.payload
            meta = {k: v for k, v in pl.items() if k not in ("text", "metadata")}
            nodes.append(TextNode(id_=str(p.id), text=pl["text"], metadata=meta))
        if offset is None:
            return nodes


def build_bm25_retriever(scope: Scope, top_k: int = 5) -> BM25Retriever:
    """Un index BM25 par périmètre d'accès (jamais de documents hors droits en mémoire)."""
    key = (_scope_key(scope), top_k)
    if key not in _bm25_cache:
        nodes = _load_scope_nodes(scope)
        if not nodes:
            raise RuntimeError("Aucun document accessible pour construire l'index BM25")
        _bm25_cache[key] = BM25Retriever.from_defaults(
            nodes=nodes, similarity_top_k=min(top_k, len(nodes)),
            stemmer=Stemmer.Stemmer("french"), language="fr",
        )
    return _bm25_cache[key]


def clear_bm25_cache() -> None:
    """À appeler après une ré-ingestion si le process reste actif."""
    _bm25_cache.clear()


FUSION_QUERY_PROMPT = (
    "Tu génères des requêtes de recherche documentaire (banque / réglementation COBAC). "
    "Génère {num_queries} requêtes de recherche différentes, une par ligne, sans numérotation, "
    "liées à la requête suivante :\nRequête : {query}\nRequêtes :\n"
)


def build_fusion_retriever(scope: Scope, top_k: int = 5, num_queries: int = 4) -> QueryFusionRetriever:
    fetch_k = top_k * 4
    return QueryFusionRetriever(
        [MMRVectorRetriever(build_access_filter(scope), top_k=fetch_k, fetch_k=fetch_k * 2),
         build_bm25_retriever(scope, fetch_k)],
        llm=_llama_llm(),
        query_gen_prompt=FUSION_QUERY_PROMPT,
        mode=FUSION_MODES.RECIPROCAL_RANK,
        similarity_top_k=top_k,
        num_queries=num_queries,  # inclut la requête d'origine
        use_async=False,
    )


# =====================================================================
# 5. Retrievers LangChain
# =====================================================================
@lru_cache(maxsize=1)
def _lc_vectorstore() -> LCQdrantVectorStore:
    return LCQdrantVectorStore(
        client=_client(),
        collection_name=COLLECTION_NAME,
        embedding=LlamaIndexEmbeddings(),
        content_payload_key="text",
        metadata_payload_key="metadata",
    )


MULTI_QUERY_PROMPT = PromptTemplate.from_template(
    "Tu es un assistant de recherche documentaire bancaire. Génère 3 reformulations différentes "
    "de la question ci-dessous (synonymes, termes réglementaires, angle différent) pour "
    "améliorer la recherche dans une base vectorielle. Une reformulation par ligne, "
    "sans numérotation ni texte additionnel.\nQuestion : {question}"
)


def build_multiquery_retriever(scope: Scope, top_k: int = 5) -> MultiQueryRetriever:
    base = _lc_vectorstore().as_retriever(
        search_type="mmr",
        search_kwargs={"k": top_k, "fetch_k": top_k * 4, "lambda_mult": 0.6,
                       "filter": build_access_filter(scope)},
    )
    return MultiQueryRetriever.from_llm(
        retriever=base, llm=_lc_llm(), prompt=MULTI_QUERY_PROMPT, include_original=True
    )


class SecureSelfQueryRetriever(SelfQueryRetriever):
    """SelfQueryRetriever dont le filtre RBAC ne peut PAS être écrasé par le LLM.

    Par défaut, le filtre généré par le LLM remplace `search_kwargs["filter"]` :
    on le combine ici (AND) avec le filtre d'accès.
    """

    access_filter: Any = None

    def _secure(self, search_kwargs: dict[str, Any]) -> dict[str, Any]:
        llm_filter = search_kwargs.get("filter")
        must = [self.access_filter] + ([llm_filter] if llm_filter is not None else [])
        return {**search_kwargs, "filter": qm.Filter(must=must)}

    def _get_docs_with_query(self, query, search_kwargs):
        return super()._get_docs_with_query(query, self._secure(search_kwargs))

    async def _aget_docs_with_query(self, query, search_kwargs):
        return await super()._aget_docs_with_query(query, self._secure(search_kwargs))


METADATA_FIELDS = [
    AttributeInfo(name="doc_type", type="string",
                  description="Type du document : 'public' (réglementaire/institutionnel) ou 'prive' (interne)"),
    AttributeInfo(name="business_layer", type="string",
                  description=(f"Couche métier : '{FRONT_OFFICE}', '{CREDIT_RISQUES}', "
                               f"'{CONFORMITE}' ou '{DIRECTION_GENERALE}'")),
    AttributeInfo(name="source", type="string", description="Nom exact du fichier PDF"),
    AttributeInfo(name="page", type="integer", description="Numéro de page dans le PDF"),
]


def build_selfquery_retriever(scope: Scope, top_k: int = 5) -> SecureSelfQueryRetriever:
    return SecureSelfQueryRetriever.from_llm(
        llm=_lc_llm(),
        vectorstore=_lc_vectorstore(),
        document_contents="Extraits de documents bancaires : règlements COBAC, procédures et politiques internes",
        metadata_field_info=METADATA_FIELDS,
        search_kwargs={"k": top_k},
        access_filter=build_access_filter(scope),
    )


# =====================================================================
# 6. Point d'entrée unique
# =====================================================================
STRATEGIES = ("vector_mmr", "multi_query", "self_query", "bm25", "fusion")

_BUILDERS = {
    "vector_mmr": lambda s, k: MMRVectorRetriever(build_access_filter(s), top_k=k, fetch_k=k * 4),
    "multi_query": build_multiquery_retriever,
    "self_query": build_selfquery_retriever,
    "bm25": build_bm25_retriever,
    "fusion": build_fusion_retriever,
}
_retriever_cache: dict[tuple, Any] = {}


def retrieve(strategy: str, query: str, user: UserContext, top_k: int = 5) -> list[RetrievedChunk]:
    if strategy not in _BUILDERS:
        raise ValueError(f"Stratégie inconnue : {strategy!r} (choix : {STRATEGIES})")

    scope = resolve_scope(user)
    key = (strategy, _scope_key(scope), top_k)
    if key not in _retriever_cache:
        _retriever_cache[key] = _BUILDERS[strategy](scope, top_k)
    retriever = _retriever_cache[key]

    t0 = time.time()
    if isinstance(retriever, LIRetriever):
        chunks = [_from_llama(n, strategy) for n in retriever.retrieve(query)]
    else:  # retriever LangChain
        chunks = [_from_langchain(d, strategy) for d in retriever.invoke(query)][:top_k]
    chunks = enforce_access(chunks, scope)

    log_event("retrieval", user, strategy=strategy, query=query,
              latency_s=round(time.time() - t0, 2),
              results=[{"source": c.source, "page": c.page, "doc_type": c.doc_type,
                        "layer": c.business_layer} for c in chunks])
    return chunks


def warmup() -> None:
    """À appeler au démarrage de l'API : charge le modèle d'embedding, vérifie Qdrant."""
    get_embed_model().get_query_embedding("warmup")
    if not _client().collection_exists(COLLECTION_NAME):
        raise RuntimeError(f"Collection Qdrant introuvable : {COLLECTION_NAME!r} (lancez l'ingestion)")
    _llama_index()