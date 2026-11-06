"""Endpoints de l'assistant RAG.

  POST /rag/chat         -> réponse complète (JSON)
  POST /rag/chat/stream  -> réponse en streaming (Server-Sent Events)

Ce module est autonome : il se branche sur l'app avec un `include_router` et une ligne
dans le lifespan (voir main.py). Il ne modifie ni `api_router` ni vos routes existantes.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# À ADAPTER : votre dépendance d'authentification existante (renvoie l'utilisateur courant)
from .dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


# ---------------------------------------------------------------------
# Cycle de vie du service (appelé depuis le lifespan de main.py)
# ---------------------------------------------------------------------
_service: Any = None


async def init_rag_service() -> None:
    """Construit le service RAG une seule fois (modèle d'embedding, Qdrant, agent)."""
    global _service
    from app.services.Agentic_RAG.rag_agent import RagService, setup_audit_logging

    setup_audit_logging()
    _service = await RagService.create()
    logger.info("Service RAG initialisé")


def get_rag_service() -> Any:
    if _service is None:
        raise HTTPException(status_code=503, detail="Assistant RAG non initialisé")
    return _service


# ---------------------------------------------------------------------
# Identité : utilisateur authentifié -> UserContext du RAG
# ---------------------------------------------------------------------
# Correspondance entre le département applicatif et les clés de ROLE_POLICIES
# (app/services/Agentic_RAG/retriever_tools.py). Un utilisateur hors de ces
# départements est refusé (403) par le RAG (deny by default).
RAG_DEPARTMENT_MAP: dict[str, str] = {
    "front office": "charge_clientele",
    "credit & risques": "credit_risques",
    "direction conformite": "conformite",
    "direction generale": "direction_generale",
}


def _normalize(value: str) -> str:
    return " ".join(
        str(value).replace("é", "e").replace("è", "e").replace("ê", "e").lower().split()
    )


def get_rag_user(current_user=Depends(get_current_user)) -> Any:
    """Mappe l'utilisateur authentifié (rôle + département) vers un UserContext RAG."""
    from app.services.Agentic_RAG.retriever_tools import UserContext

    department = getattr(current_user, "department", None)
    department_name = getattr(department, "name", None)
    role = RAG_DEPARTMENT_MAP.get(_normalize(department_name or ""))
    if role is None:
        role = getattr(current_user.role, "value", current_user.role)
        role = str(role)
    return UserContext(user_id=str(current_user.id), role=role)


# ---------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Identifiant de conversation. Omis = nouvelle conversation (renvoyé dans la réponse).",
    )


class SourceOut(BaseModel):
    id: int
    source: str
    page: int | str | None = None
    doc_type: str | None = None
    business_layer: str | None = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    sources: list[SourceOut] = []


# ---------------------------------------------------------------------
# Erreurs métier -> HTTP
# ---------------------------------------------------------------------
@contextmanager
def _map_rag_errors():
    from app.services.Agentic_RAG.rag_agent import (
        AccessDenied,
        InvalidRequest,
        RagError,
        RagTimeout,
    )

    try:
        yield
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except InvalidRequest as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RagTimeout as e:
        raise HTTPException(status_code=504, detail=str(e))
    except RagError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: Any = Depends(get_rag_user),
    service: Any = Depends(get_rag_service),
):
    with _map_rag_errors():
        result = await service.ask(user, body.question, body.session_id)
    return result.to_dict()


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    user: Any = Depends(get_rag_user),
    service: Any = Depends(get_rag_service),
):
    """Événements SSE : {"type": "session"|"token"|"sources"|"done"|"error", ...}"""
    events = service.stream(user, body.question, body.session_id)

    # Le 1er événement ("session") n'est émis qu'après les validations (rôle, question,
    # session_id) : on le lit AVANT d'ouvrir le flux pour renvoyer un vrai 403/422.
    with _map_rag_errors():
        first = await anext(events)

    async def event_stream():
        from app.services.Agentic_RAG.rag_agent import RagError

        yield _sse(first)
        try:
            async for event in events:
                yield _sse(event)
        except RagError as e:  # le statut 200 est déjà parti : on signale l'erreur dans le flux
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
