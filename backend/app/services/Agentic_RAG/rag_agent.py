"""Service RAG central, destiné à être appelé par l'API FastAPI.

Responsabilités :
  - construire UNE fois l'agent (create_agent) et le partager entre toutes les requêtes ;
  - isoler les conversations par utilisateur (thread_id = user_id:session_id) ;
  - sérialiser les requêtes d'une même session, borner la concurrence globale, les durées
    et le nombre d'étapes de l'agent ;
  - ne jamais exposer d'erreur interne à l'appelant (exceptions métier uniquement) ;
  - ouvrir UN LLMRequestContext (SEC-01) par requête, dans ask et stream : tous les appels
    au fournisseur de la requête (étapes de l'agent, outils, retrievers imbriqués)
    partagent son request_id, son coffre et ses limites (llm_adapters.py) ;
  - remettre ce contexte, une fois fermé et s'il contient des comptes rendus d'appels, au
    crochet d'audit de l'appelant (``audit``), y compris en cas d'erreur, de timeout ou de
    limite atteinte. L'API l'écrit dans le journal central (audit.log_request_calls) :
    il n'existe aucun autre journal du RAG.

L'authentification n'est PAS faite ici : l'API doit fournir un `UserContext` issu d'un jeton
vérifié (jamais d'un champ du corps de la requête).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

from langchain.agents import create_agent
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from app.core.config import settings
from app.services.Agentic_RAG.llm_adapters import GatewayChatModel
from app.services.Agentic_RAG.memory_tools import MEMORY_TOOLS, build_memory_store
from app.services.Agentic_RAG.retriever_tools import UserContext, resolve_scope, warmup
from app.services.Agentic_RAG.search_tools import SEARCH_TOOLS
from app.services.Agentic_RAG.system_prompt import SYSTEM_PROMPT
from app.services.llm_request import (
    CallKind,
    CallLimitExceeded,
    LLMRequestContext,
    PromptTooLong,
    RequestLimits,
)

logger = logging.getLogger(__name__)

# Reçoit le contexte fermé d'une requête qui a produit au moins un compte rendu d'appel.
AuditHook = Callable[[LLMRequestContext], None]


@contextmanager
def _streamed_request(context: LLMRequestContext) -> Iterator[LLMRequestContext]:
    """``with context`` pour un générateur asynchrone, qui peut être fermé ailleurs.

    Un flux interrompu (client déconnecté) est finalisé plus tard, souvent depuis un autre
    ``contextvars.Context`` : ``LLMRequestContext.__exit__`` ferme alors le contexte (coffre
    supprimé), mais la remise à zéro du ContextVar y lève ``ValueError``. Seul ce cas est
    toléré, et seulement si le contexte est bien fermé : sinon la ValueError remplacerait
    le GeneratorExit et ``aclose()`` échouerait par une fausse erreur de l'agent.
    """
    context.__enter__()
    try:
        yield context
    finally:
        try:
            context.__exit__(None, None, None)
        except ValueError:
            if not context.closed:
                raise


TOOLS = SEARCH_TOOLS + MEMORY_TOOLS

MAX_QUESTION_CHARS = 4000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FALLBACK_ANSWER = "Je n'ai pas pu produire de réponse. Reformulez votre question."
TOO_COMPLEX = "La question est trop complexe, reformulez-la."
UNAVAILABLE = "Le service est momentanément indisponible."


# --------------------------------------------------------------------
# Exceptions métier (à mapper en codes HTTP dans l'API)
# --------------------------------------------------------------------
class RagError(Exception):
    """Erreur générique du service (HTTP 500/502). Le message est sûr à afficher."""


class AccessDenied(RagError):
    """Rôle inconnu ou sans droits (HTTP 403)."""


class InvalidRequest(RagError):
    """Question ou session_id invalide (HTTP 422)."""


class RagTimeout(RagError):
    """Durée maximale dépassée (HTTP 504)."""


# --------------------------------------------------------------------
# Réponse
# --------------------------------------------------------------------
@dataclass
class AgentAnswer:
    answer: str
    session_id: str
    sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _turn_messages(messages: list) -> list:
    """Messages du tour courant : tout ce qui suit la dernière question utilisateur."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1 :]
    return messages


def _turn_sources(turn: list) -> list[dict]:
    """Sources du tour = artifacts des ToolMessage (jamais vus par le LLM)."""
    return [
        s
        for m in turn
        if isinstance(m, ToolMessage) and isinstance(m.artifact, list)
        for s in m.artifact
    ]


# --------------------------------------------------------------------
# Service
# --------------------------------------------------------------------
class RagService:
    """Utiliser `await RagService.create()` (pas le constructeur) au démarrage de l'API."""

    def __init__(
        self,
        *,
        store=None,
        checkpointer=None,
        timeout_s: float = 90.0,
        recursion_limit: int = 12,
        max_concurrency: int = 20,
    ):
        self._store = store
        self._checkpointer = checkpointer
        self.timeout_s = timeout_s
        self.recursion_limit = recursion_limit
        self._sem = asyncio.Semaphore(max_concurrency)
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self.agent = None

    @classmethod
    async def create(cls, **kwargs) -> RagService:
        service = cls(**kwargs)
        # chargements lourds hors boucle d'événements
        await asyncio.to_thread(service._init_blocking)
        return service

    def _init_blocking(self) -> None:
        warmup()  # modèle d'embedding + Qdrant : échec rapide si la config est mauvaise
        self._build_agent()

    def _build_agent(self) -> None:
        self.agent = create_agent(
            # AGENT_STEP : Groq via le contexte SEC-01 de la requête (aucun client direct)
            model=GatewayChatModel(kind=CallKind.AGENT_STEP, max_tokens=2048),
            tools=TOOLS,
            system_prompt=SYSTEM_PROMPT,
            context_schema=UserContext,
            checkpointer=self._checkpointer or InMemorySaver(),  # mémoire court terme
            store=self._store or build_memory_store(),  # mémoire long terme
        )

    @staticmethod
    def _request_context(user: UserContext) -> LLMRequestContext:
        """Le contexte SEC-01 d'une requête : un par appel à ask / stream, jamais par outil."""
        return LLMRequestContext(
            caller_id=user.user_id,
            limits=RequestLimits(
                max_provider_calls=settings.max_provider_calls,
                max_prompt_chars=settings.max_prompt_chars,
                timeout_seconds=settings.timeout_seconds,
            ),
        )

    # ---------- préparation / garde-fous ----------
    def _prepare(self, user: UserContext, question: str, session_id: str | None):
        try:
            resolve_scope(user)
        except PermissionError as e:
            raise AccessDenied("Accès refusé.") from e

        question = (question or "").strip()
        if not question:
            raise InvalidRequest("La question est vide.")
        if len(question) > MAX_QUESTION_CHARS:
            raise InvalidRequest(f"Question trop longue (max {MAX_QUESTION_CHARS} caractères).")

        session_id = session_id or uuid.uuid4().hex
        if not SESSION_ID_PATTERN.match(session_id):
            raise InvalidRequest("session_id invalide (lettres, chiffres, '-' et '_', 64 max).")

        thread_id = f"{user.user_id}:{session_id}"  # le préfixe user_id interdit tout accès croisé
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": self.recursion_limit}
        return question, session_id, thread_id, config

    def _lock_for(self, thread_id: str) -> asyncio.Lock:
        """Un verrou par session : deux requêtes simultanées ne se mélangent pas dans l'historique."""
        lock = self._locks.get(thread_id)
        if lock is None:
            lock = self._locks[thread_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _finish(session_id, messages) -> AgentAnswer:
        sources = _turn_sources(_turn_messages(messages))
        answer = str(messages[-1].text).strip() if messages else ""
        return AgentAnswer(answer=answer or FALLBACK_ANSWER, session_id=session_id, sources=sources)

    @staticmethod
    def _hand_over(context: LLMRequestContext, audit: AuditHook | None) -> None:
        """Remet le contexte fermé au crochet d'audit, s'il a produit des comptes rendus.

        Appelé dans un ``finally`` : succès, erreur, timeout ou limite atteinte. Une requête
        sans appel au fournisseur ne produit aucun compte rendu, donc aucun événement.
        """
        context.close()  # idempotent : la fermeture précède toujours la journalisation
        if audit is not None and context.reports():
            audit(context)

    # ---------- API publique ----------
    async def ask(
        self,
        user: UserContext,
        question: str,
        session_id: str | None = None,
        *,
        audit: AuditHook | None = None,
    ) -> AgentAnswer:
        question, session_id, thread_id, config = self._prepare(user, question, session_id)
        context = self._request_context(user)
        try:
            async with self._sem, self._lock_for(thread_id):
                # Le contexte est actif avant la création de la tâche de wait_for : la tâche,
                # les nœuds LangGraph et les outils l'héritent (ContextVar copié).
                with context:
                    result = await asyncio.wait_for(
                        self.agent.ainvoke(
                            {"messages": [HumanMessage(question)]}, config=config, context=user
                        ),
                        timeout=self.timeout_s,
                    )
        except TimeoutError as e:
            raise RagTimeout("La réponse a pris trop de temps.") from e
        except (GraphRecursionError, CallLimitExceeded, PromptTooLong) as e:
            raise RagError(TOO_COMPLEX) from e
        except Exception as e:
            logger.error("Erreur agent : %s", type(e).__name__)  # jamais le message
            raise RagError(UNAVAILABLE) from e
        finally:
            self._hand_over(context, audit)
        return self._finish(session_id, result["messages"])

    async def stream(
        self,
        user: UserContext,
        question: str,
        session_id: str | None = None,
        *,
        audit: AuditHook | None = None,
    ) -> AsyncIterator[dict]:
        """Événements pour SSE/WebSocket :
        {"type": "session"|"token"|"sources"|"done", ...}  — ou lève une RagError.
        """
        question, session_id, thread_id, config = self._prepare(user, question, session_id)
        t0 = time.time()
        yield {"type": "session", "session_id": session_id}
        context = self._request_context(user)
        try:
            async with self._sem, self._lock_for(thread_id):
                # Ouvert après l'événement "session" : tout le flux de l'agent s'exécute dans
                # la même tâche consommatrice, qui ferme le contexte à la sortie du bloc.
                # Les fragments "model" sont les réponses déjà restaurées (llm_adapters).
                with _streamed_request(context):
                    async for chunk, meta in self.agent.astream(
                        {"messages": [HumanMessage(question)]},
                        config=config,
                        context=user,
                        stream_mode="messages",
                    ):
                        if time.time() - t0 > self.timeout_s:
                            raise TimeoutError
                        is_model_chunk = meta.get("langgraph_node") == "model" and isinstance(
                            chunk, AIMessageChunk
                        )
                        if is_model_chunk and (text := str(chunk.text)):
                            yield {"type": "token", "text": text}
                state = await self.agent.aget_state(config)
        except TimeoutError as e:
            raise RagTimeout("La réponse a pris trop de temps.") from e
        except (GraphRecursionError, CallLimitExceeded, PromptTooLong) as e:
            raise RagError(TOO_COMPLEX) from e
        except Exception as e:
            logger.error("Erreur agent (stream) : %s", type(e).__name__)  # jamais le message
            raise RagError(UNAVAILABLE) from e
        finally:
            # Aussi à la fermeture anticipée du flux (client déconnecté) : GeneratorExit.
            self._hand_over(context, audit)

        final = self._finish(session_id, state.values["messages"])
        yield {"type": "sources", "sources": final.sources}
        yield {"type": "done"}
