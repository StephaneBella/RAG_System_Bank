"""Service RAG central, destiné à être appelé par l'API FastAPI.

Responsabilités :
  - construire UNE fois l'agent (create_agent) et le partager entre toutes les requêtes ;
  - isoler les conversations par utilisateur (thread_id = user_id:session_id) ;
  - sérialiser les requêtes d'une même session, borner la concurrence globale, les durées
    et le nombre d'étapes de l'agent ;
  - ne jamais exposer d'erreur interne à l'appelant (exceptions métier uniquement) ;
  - journaliser chaque réponse (audit).

L'authentification n'est PAS faite ici : l'API doit fournir un `UserContext` issu d'un jeton
vérifié (jamais d'un champ du corps de la requête).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
import weakref
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from memory_tools import MEMORY_TOOLS, build_memory_store
from system_prompt import SYSTEM_PROMPT
from retriever_tools import UserContext, log_event, resolve_scope, warmup
from search_tools import SEARCH_TOOLS

load_dotenv()
logger = logging.getLogger(__name__)

LLM_MODEL = os.getenv("LLM_MODEL", "groq:openai-gpt-oss-102b")
TOOLS = SEARCH_TOOLS + MEMORY_TOOLS

MAX_QUESTION_CHARS = 4000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FALLBACK_ANSWER = "Je n'ai pas pu produire de réponse. Reformulez votre question."


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
            return messages[i + 1:]
    return messages


def _turn_sources(turn: list) -> list[dict]:
    """Sources du tour = artifacts des ToolMessage (jamais vus par le LLM)."""
    return [s for m in turn if isinstance(m, ToolMessage) and isinstance(m.artifact, list) for s in m.artifact]


def _turn_tools(turn: list) -> list[str]:
    return [c["name"] for m in turn if isinstance(m, AIMessage) for c in m.tool_calls]


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
    async def create(cls, **kwargs) -> "RagService":
        service = cls(**kwargs)
        await asyncio.to_thread(service._init_blocking)  # chargements lourds hors boucle d'événements
        return service

    def _init_blocking(self) -> None:
        warmup()  # modèle d'embedding + Qdrant : échec rapide si la config est mauvaise
        self.agent = create_agent(
            model=ChatGroq(model=LLM_MODEL, temperature=0, max_tokens=2048),
            tools=TOOLS,
            system_prompt=SYSTEM_PROMPT,
            context_schema=UserContext,
            checkpointer=self._checkpointer or InMemorySaver(),  # mémoire court terme
            store=self._store or build_memory_store(),           # mémoire long terme
        )

    # ---------- préparation / garde-fous ----------
    def _prepare(self, user: UserContext, question: str, session_id: str | None):
        try:
            resolve_scope(user)
        except PermissionError as e:
            log_event("access_denied", user)
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

    def _finish(self, user, question, session_id, messages, t0) -> AgentAnswer:
        turn = _turn_messages(messages)
        sources = _turn_sources(turn)
        answer = str(messages[-1].text).strip() if messages else ""
        log_event("answer", user, session_id=session_id, question=question, sources=sources,
                  tools=_turn_tools(turn), latency_s=round(time.time() - t0, 2))
        return AgentAnswer(answer=answer or FALLBACK_ANSWER, session_id=session_id, sources=sources)

    # ---------- API publique ----------
    async def ask(self, user: UserContext, question: str, session_id: str | None = None) -> AgentAnswer:
        question, session_id, thread_id, config = self._prepare(user, question, session_id)
        t0 = time.time()
        try:
            async with self._sem, self._lock_for(thread_id):
                result = await asyncio.wait_for(
                    self.agent.ainvoke({"messages": [HumanMessage(question)]}, config=config, context=user),
                    timeout=self.timeout_s,
                )
        except asyncio.TimeoutError as e:
            log_event("answer_timeout", user, session_id=session_id)
            raise RagTimeout("La réponse a pris trop de temps.") from e
        except GraphRecursionError as e:
            log_event("answer_recursion_limit", user, session_id=session_id)
            raise RagError("La question est trop complexe, reformulez-la.") from e
        except Exception as e:
            logger.exception("Erreur agent")
            raise RagError("Le service est momentanément indisponible.") from e
        return self._finish(user, question, session_id, result["messages"], t0)

    async def stream(self, user: UserContext, question: str, session_id: str | None = None) -> AsyncIterator[dict]:
        """Événements pour SSE/WebSocket :
        {"type": "session"|"token"|"sources"|"done", ...}  — ou lève une RagError.
        """
        question, session_id, thread_id, config = self._prepare(user, question, session_id)
        t0 = time.time()
        yield {"type": "session", "session_id": session_id}
        try:
            async with self._sem, self._lock_for(thread_id):
                async for chunk, meta in self.agent.astream(
                    {"messages": [HumanMessage(question)]}, config=config, context=user,
                    stream_mode="messages",
                ):
                    if time.time() - t0 > self.timeout_s:
                        raise asyncio.TimeoutError
                    if meta.get("langgraph_node") == "model" and isinstance(chunk, AIMessageChunk):
                        if text := str(chunk.text):
                            yield {"type": "token", "text": text}
                state = await self.agent.aget_state(config)
        except asyncio.TimeoutError as e:
            log_event("answer_timeout", user, session_id=session_id)
            raise RagTimeout("La réponse a pris trop de temps.") from e
        except GraphRecursionError as e:
            raise RagError("La question est trop complexe, reformulez-la.") from e
        except Exception as e:
            logger.exception("Erreur agent (stream)")
            raise RagError("Le service est momentanément indisponible.") from e

        final = self._finish(user, question, session_id, state.values["messages"], t0)
        yield {"type": "sources", "sources": final.sources}
        yield {"type": "done"}


def setup_audit_logging(path: str = "logs/audit.jsonl") -> None:
    """À appeler une fois au démarrage de l'API (idempotent)."""
    audit = logging.getLogger("rag.audit")
    if any(isinstance(h, logging.FileHandler) for h in audit.handlers):
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    audit.propagate = False