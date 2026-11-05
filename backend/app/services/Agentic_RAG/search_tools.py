"""Outils de recherche documentaire : une fabrique, un @tool par stratégie.

Chaque recherche suit : retrieve (filtre d'accès dans Qdrant + enforce_access)
-> screen_chunks (anti-injection) -> rag_context.build_context (minimisation T4)
-> format_extracts. Rien n'atteint le LLM sans passer par rag_context.build_context.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool

from app.services import rag_context
from app.services.Agentic_RAG.augmentation_tool import format_extracts, screen_chunks
from app.services.Agentic_RAG.retriever_tools import (
    STRATEGIES,
    ScopedChunk,
    UserContext,
    resolve_scope,
    retrieve,
)
from app.services.Agentic_RAG.system_prompt import SEARCH_TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)

SEARCH_UNAVAILABLE = "La recherche documentaire est indisponible pour le moment."

# Limites T4 fixées par l'appelant (décision H6, pas de défaut dans rag_context) : valeurs
# existantes de l'agent (top_k de retrieve, budget de l'ancien augmentation_tool).
MAX_EXTRACTS = 5
MAX_CONTEXT_CHARS = 8000
# MIN-DOC-04 : la note C3 est exclue de toute transmission par défaut.
EXCLUDED_SOURCES = frozenset({"Prive__Direction_Generale_Strategie.pdf"})


def build_search_context(chunks: Sequence[ScopedChunk], user: UserContext) -> tuple[str, list]:
    """Contexte envoyé au LLM, construit par ``rag_context.build_context`` uniquement.

    Les couches autorisées sont recalculées depuis l'utilisateur (refus si aucune).
    Aucun ``vault`` ici : la pseudonymisation est faite au moment de l'envoi au
    fournisseur (LLMRequestContext.protect, adaptateur à venir).
    """
    scope = resolve_scope(user)
    minimized = rag_context.build_context(
        screen_chunks(chunks),
        allowed_layers=sorted(set().union(*scope.values())),
        excluded_sources=EXCLUDED_SOURCES,
        max_extracts=MAX_EXTRACTS,
        max_context_chars=MAX_CONTEXT_CHARS,
        include_classification=False,
    )
    return format_extracts(minimized.extracts)


def _make_search_tool(strategy: str):
    # response_format="content_and_artifact" :
    #   content  -> contexte minimisé envoyé au LLM
    #   artifact -> liste des sources, gardée dans le ToolMessage (jamais vue par le LLM)
    @tool(
        f"search_{strategy}",
        description=SEARCH_TOOL_DESCRIPTIONS[strategy],
        response_format="content_and_artifact",
    )
    def search(query: str, runtime: ToolRuntime[UserContext]) -> tuple[str, list[dict]]:
        try:
            return build_search_context(retrieve(strategy, query, runtime.context), runtime.context)
        # Aucune erreur ne doit arrêter l'agent ni exposer un détail : le type seulement,
        # car un message peut contenir la requête ou un extrait.
        except Exception as error:  # noqa: BLE001
            logger.error("Échec de la recherche (%s) : %s", strategy, type(error).__name__)
            return SEARCH_UNAVAILABLE, []  # message neutre : aucun détail interne exposé

    return search


SEARCH_TOOLS = [_make_search_tool(s) for s in STRATEGIES]
