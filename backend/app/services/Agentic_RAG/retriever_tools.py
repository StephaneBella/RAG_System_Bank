"""Retrievers (LlamaIndex + LangChain) et RBAC.

Stratégies exposées via `retrieve(strategy, query, user)` :
  - vector_mmr  : LlamaIndex VectorIndexRetriever + MMR
  - multi_query : LangChain MultiQueryRetriever (base = Qdrant MMR)
  - self_query  : LangChain SelfQueryRetriever (filtres de métadonnées déduits par le LLM)
  - bm25        : LlamaIndex BM25Retriever (lexical)
  - fusion      : LlamaIndex QueryFusionRetriever (MMR vectoriel + BM25, RRF)

Sécurité : le filtre d'accès (RBAC) est appliqué DANS la requête Qdrant (et à la
construction de l'index BM25), puis revérifié sur les résultats (défense en profondeur).
Self-query : uniquement SecureSelfQueryRetriever (filtre d'accès ET filtre du LLM).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any

import numpy as np
import Stemmer
from langchain_classic.chains.query_constructor.schema import AttributeInfo
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_classic.retrievers.self_query.base import SelfQueryRetriever
from langchain_community.query_constructors.qdrant import QdrantTranslator
from langchain_core.embeddings import Embeddings
from langchain_core.prompts import PromptTemplate
from langchain_qdrant import QdrantVectorStore as LCQdrantVectorStore
from llama_index.core import VectorStoreIndex
from llama_index.core.base.base_retriever import BaseRetriever as LIRetriever
from llama_index.core.retrievers import QueryFusionRetriever, VectorIndexRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle, TextNode
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.vector_stores.qdrant import QdrantVectorStore as LIQdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.core.config import settings
from app.services.Agentic_RAG.Data_preparation.ingestion import (
    CONFORMITE,
    CREDIT_RISQUES,
    DIRECTION_GENERALE,
    FRONT_OFFICE,
    PRIVE,
    PUBLIC,
)
from app.services.Agentic_RAG.Data_preparation.vectorisation import get_embed_model
from app.services.Agentic_RAG.llm_adapters import GatewayChatModel, GatewayLlamaLLM
from app.services.llm_request import CallKind

logger = logging.getLogger(__name__)

# Même collection que l'ingestion (variable QDRANT_COLLECTION, lue par Settings)
COLLECTION_NAME = settings.qdrant_collection


# =====================================================================
# 1. Périmètre d'accès : département -> couche (SEC-02)
# =====================================================================
ALL_LAYERS = frozenset({FRONT_OFFICE, CREDIT_RISQUES, CONFORMITE, DIRECTION_GENERALE})
DOC_TYPES = frozenset({PUBLIC, PRIVE})

# Nom exact du département en base (app/db/seed.py) -> couche RAG (ingestion.py).
# VIDE à dessein : la correspondance couches <-> départements n'est pas décidée
# (décision H10, écart E3 de docs/US-07-politique-minimisation.md ; SEC-02 § 4). Les noms
# diffèrent ("Credit & Risques" / credit_risques, "Direction Conformite" / conformite_lbc_ft)
# et aucune table n'existe : tout département sans entrée est refusé, jamais élargi.
# Le périmètre de l'ADMIN via le chat (H11) n'est pas décidé non plus : pas d'exception.
DEPARTMENT_LAYERS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class UserContext:
    """Identité de l'appelant, construite côté serveur depuis l'utilisateur authentifié."""

    user_id: int
    department: str | None


Scope = dict[str, frozenset]  # doc_type -> couches autorisées


def resolve_scope(user: UserContext) -> Scope:
    """Deny by default : département sans correspondance => PermissionError.

    Règle SEC-02 (option A) : un utilisateur n'accède qu'à la couche de son propre
    département, quelle que soit la visibilité ; le public n'ouvre aucun accès à une
    autre couche. Le message d'erreur ne cite ni le département ni la couche.
    """
    if not isinstance(user, UserContext) or type(user.user_id) is not int:
        raise PermissionError("Appelant invalide")
    layer = DEPARTMENT_LAYERS.get(user.department) if user.department else None
    if layer not in ALL_LAYERS:
        raise PermissionError("Aucun périmètre d'accès pour cet utilisateur")
    return {doc_type: frozenset({layer}) for doc_type in DOC_TYPES}


def build_access_filter(scope: Scope) -> qm.Filter:
    """Filtre Qdrant : OR de (doc_type = X AND business_layer IN couches)."""
    clauses = [
        qm.Filter(
            must=[
                qm.FieldCondition(key="doc_type", match=qm.MatchValue(value=dt)),
                qm.FieldCondition(key="business_layer", match=qm.MatchAny(any=sorted(layers))),
            ]
        )
        for dt, layers in scope.items()
    ]
    if not clauses:
        raise PermissionError("Périmètre d'accès vide")
    return qm.Filter(should=clauses)


def _scope_key(scope: Scope) -> tuple:
    return tuple(sorted((dt, tuple(sorted(layers))) for dt, layers in scope.items()))


# =====================================================================
# 2. Résultat normalisé
# =====================================================================
@dataclass
class ScopedChunk:
    """Extrait renvoyé par un retriever, avant la minimisation de rag_context (T4).

    Représentation interne à la récupération : seul ``rag_context.RetrievedChunk`` est
    transmis à ``rag_context.build_context`` (voir search_tools).
    """

    text: str
    source: str
    page: int | str | None
    doc_type: str | None
    business_layer: str | None
    score: float | None
    retriever: str
    chunk_id: str | None = None


def _from_llama(n: NodeWithScore, retriever: str) -> ScopedChunk:
    m = n.node.metadata
    return ScopedChunk(
        text=n.node.get_content(metadata_mode=MetadataMode.NONE),
        source=m.get("source", "inconnu"),
        page=m.get("page"),
        doc_type=m.get("doc_type"),
        business_layer=m.get("business_layer"),
        score=n.score,
        retriever=retriever,
        chunk_id=n.node.node_id,
    )


def _from_langchain(doc, retriever: str) -> ScopedChunk:
    m = doc.metadata or {}
    return ScopedChunk(
        text=doc.page_content,
        source=m.get("source", "inconnu"),
        page=m.get("page"),
        doc_type=m.get("doc_type"),
        business_layer=m.get("business_layer"),
        score=None,
        retriever=retriever,
        chunk_id=str(getattr(doc, "id", None) or ""),
    )


def enforce_access(chunks: list[ScopedChunk], scope: Scope) -> list[ScopedChunk]:
    """Dernier garde-fou : élimine tout chunk hors périmètre avant le LLM."""
    safe = [c for c in chunks if c.business_layer in scope.get(c.doc_type, ())]
    if len(safe) != len(chunks):
        logger.warning("%d chunk(s) hors périmètre éliminés", len(chunks) - len(safe))
    return safe


# =====================================================================
# 3. Ressources partagées
# =====================================================================
@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    """Client Qdrant construit depuis Settings (mêmes exigences que l'ingestion)."""
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise RuntimeError("qdrant_url et qdrant_api_key doivent être définis")
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value())


def _llama_llm() -> GatewayLlamaLLM:
    """LLM LlamaIndex de la fusion de requêtes (QUERY_FUSION) : Groq via le contexte SEC-01.

    Toujours explicite : sans LLM, LlamaIndex prendrait son LLM par défaut (OpenAI).
    """
    return GatewayLlamaLLM(max_tokens=1024)


def _lc_llm(kind: CallKind) -> GatewayChatModel:
    """LLM LangChain des retrievers (QUERY_REWRITE, SELF_QUERY) : Groq via SEC-01."""
    return GatewayChatModel(kind=kind, max_tokens=1024)


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

    def __init__(
        self, access_filter: qm.Filter, top_k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.6
    ):
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
                key=lambda i: (
                    self._lambda * sim_q[i]
                    - (1 - self._lambda) * max(sim_dd[i][j] for j in selected)
                ),
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
            COLLECTION_NAME,
            scroll_filter=flt,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
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
            nodes=nodes,
            similarity_top_k=min(top_k, len(nodes)),
            stemmer=Stemmer.Stemmer("french"),
            language="fr",
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


def build_fusion_retriever(
    scope: Scope, top_k: int = 5, num_queries: int = 4
) -> QueryFusionRetriever:
    fetch_k = top_k * 4
    return QueryFusionRetriever(
        [
            MMRVectorRetriever(build_access_filter(scope), top_k=fetch_k, fetch_k=fetch_k * 2),
            build_bm25_retriever(scope, fetch_k),
        ],
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
        search_kwargs={
            "k": top_k,
            "fetch_k": top_k * 4,
            "lambda_mult": 0.6,
            "filter": build_access_filter(scope),
        },
    )
    return MultiQueryRetriever.from_llm(
        retriever=base,
        llm=_lc_llm(CallKind.QUERY_REWRITE),
        prompt=MULTI_QUERY_PROMPT,
        include_original=True,
    )


class SecureSelfQueryRetriever(SelfQueryRetriever):
    """SelfQueryRetriever dont le filtre RBAC ne peut PAS être écrasé par le LLM.

    Par défaut (langchain-classic 1.0.8, ``_prepare_query``), le filtre généré par le LLM
    remplace ``search_kwargs["filter"]`` : on le combine ici (AND) avec le filtre d'accès.
    Sans filtre d'accès, aucune recherche n'est faite (échec fermé).
    """

    access_filter: qm.Filter | None = None

    def _secure(self, search_kwargs: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(self.access_filter, qm.Filter):
            raise PermissionError("SecureSelfQueryRetriever requires an access filter")
        llm_filter = search_kwargs.get("filter")
        must = [self.access_filter] + ([llm_filter] if llm_filter is not None else [])
        return {**search_kwargs, "filter": qm.Filter(must=must)}

    def _get_docs_with_query(self, query, search_kwargs):
        return super()._get_docs_with_query(query, self._secure(search_kwargs))

    async def _aget_docs_with_query(self, query, search_kwargs):
        return await super()._aget_docs_with_query(query, self._secure(search_kwargs))


METADATA_FIELDS = [
    AttributeInfo(
        name="doc_type",
        type="string",
        description="Type du document : 'public' (réglementaire/institutionnel) ou 'prive' (interne)",
    ),
    AttributeInfo(
        name="business_layer",
        type="string",
        description=(
            f"Couche métier : '{FRONT_OFFICE}', '{CREDIT_RISQUES}', "
            f"'{CONFORMITE}' ou '{DIRECTION_GENERALE}'"
        ),
    ),
    AttributeInfo(name="source", type="string", description="Nom exact du fichier PDF"),
    AttributeInfo(name="page", type="integer", description="Numéro de page dans le PDF"),
]


def build_selfquery_retriever(scope: Scope, top_k: int = 5) -> SecureSelfQueryRetriever:
    # Traducteur explicite : sans lui, from_llm appelle _get_builtin_translator, qui échoue
    # (ImportError DatabricksVectorSearch, langchain-classic 1.0.8 / langchain-community 0.4.2).
    # metadata_key = clé de la copie imbriquée du payload (storing.NESTED_METADATA_KEYS).
    return SecureSelfQueryRetriever.from_llm(
        llm=_lc_llm(CallKind.SELF_QUERY),
        vectorstore=_lc_vectorstore(),
        document_contents=(
            "Extraits de documents bancaires : règlements COBAC, procédures et politiques internes"
        ),
        metadata_field_info=METADATA_FIELDS,
        structured_query_translator=QdrantTranslator(metadata_key="metadata"),
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


def retrieve(strategy: str, query: str, user: UserContext, top_k: int = 5) -> list[ScopedChunk]:
    if strategy not in _BUILDERS:
        raise ValueError(f"Stratégie inconnue : {strategy!r} (choix : {STRATEGIES})")

    scope = resolve_scope(user)
    key = (strategy, _scope_key(scope), top_k)
    if key not in _retriever_cache:
        _retriever_cache[key] = _BUILDERS[strategy](scope, top_k)
    retriever = _retriever_cache[key]

    if isinstance(retriever, LIRetriever):
        chunks = [_from_llama(n, strategy) for n in retriever.retrieve(query)]
    else:  # retriever LangChain
        chunks = [_from_langchain(d, strategy) for d in retriever.invoke(query)][:top_k]
    return enforce_access(chunks, scope)


def warmup() -> None:
    """À appeler au démarrage de l'API : charge le modèle d'embedding, vérifie Qdrant."""
    get_embed_model().get_query_embedding("warmup")
    if not _client().collection_exists(COLLECTION_NAME):
        raise RuntimeError(
            f"Collection Qdrant introuvable : {COLLECTION_NAME!r} (lancez l'ingestion)"
        )
    _llama_index()
