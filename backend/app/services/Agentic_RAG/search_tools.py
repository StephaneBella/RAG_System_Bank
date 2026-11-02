"""Outils de recherche documentaire : une fabrique, un @tool par stratégie."""
from __future__ import annotations

import logging

from langchain.tools import ToolRuntime, tool

from augmentation_tool import build_context
from system_prompt import SEARCH_TOOL_DESCRIPTIONS
from retriever_tools import STRATEGIES, UserContext, retrieve

logger = logging.getLogger(__name__)

SEARCH_UNAVAILABLE = "La recherche documentaire est indisponible pour le moment."


def _make_search_tool(strategy: str):
    # response_format="content_and_artifact" :
    #   content  -> contexte augmenté envoyé au LLM
    #   artifact -> liste des sources, gardée dans le ToolMessage (jamais vue par le LLM)
    @tool(f"search_{strategy}", description=SEARCH_TOOL_DESCRIPTIONS[strategy],
          response_format="content_and_artifact")
    def search(query: str, runtime: ToolRuntime[UserContext]) -> tuple[str, list[dict]]:
        try:
            ctx = build_context(retrieve(strategy, query, runtime.context))
            return ctx.text, ctx.sources
        except Exception:
            logger.exception("Échec de la recherche (%s)", strategy)
            return SEARCH_UNAVAILABLE, []  # message neutre : aucun détail interne exposé

    return search


SEARCH_TOOLS = [_make_search_tool(s) for s in STRATEGIES]