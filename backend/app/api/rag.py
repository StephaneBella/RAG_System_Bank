"""Endpoints de l'assistant RAG.

  POST /rag/chat         -> réponse complète (JSON)
  POST /rag/chat/stream  -> réponse en streaming (Server-Sent Events)

Importé par ``app.main`` uniquement si ``settings.rag_enabled`` est vrai (son import charge
LangChain, LlamaIndex, Qdrant et torch). Le service est construit au démarrage
(``init_rag_service``) et libéré à l'arrêt (``close_rag_service``) ; tant qu'il n'existe
pas, les routes répondent 503 avant toute authentification. Sinon, ``app.api.rag_unavailable``
expose les mêmes chemins en 503.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.dependencies import _reject, require_permission
from app.api.rag_unavailable import RAG_UNAVAILABLE
from app.api.routes.documents import ROLE_PERMISSIONS as DOCUMENT_ROLE_PERMISSIONS
from app.db.session import get_db
from app.models.audit_log import AuditAction, AuditReason, ResourceType
from app.models.user import User
from app.services.Agentic_RAG.rag_agent import (
    AccessDenied,
    AuditHook,
    InvalidRequest,
    RagError,
    RagService,
    RagTimeout,
)
from app.services.Agentic_RAG.retriever_tools import UserContext, resolve_scope
from app.services.audit import log_request_calls
from app.services.llm_request import LLMRequestContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


# ---------------------------------------------------------------------
# Cycle de vie du service (appelé depuis le lifespan de main.py)
# ---------------------------------------------------------------------
_service: RagService | None = None


async def init_rag_service() -> None:
    """Construit le service RAG une seule fois (modèle d'embedding, Qdrant, agent)."""
    global _service
    _service = await RagService.create()
    logger.info("Service RAG initialisé")


def close_rag_service() -> None:
    """Appelé à l'arrêt de l'application : le service n'est plus servi."""
    global _service
    _service = None


def get_rag_service() -> RagService:
    """Première dépendance des routes : 503 avant l'authentification si le RAG est absent."""
    if _service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=RAG_UNAVAILABLE)
    return _service


# ---------------------------------------------------------------------
# Identité et autorisation : utilisateur authentifié -> UserContext du RAG
# ---------------------------------------------------------------------
# Le chat interroge le corpus documentaire : même permission "search" que la recherche de
# documents (mécanisme central require_permission, refus ACCESS_DENIED / ROLE_FORBIDDEN).
rag_search = require_permission(
    "search", DOCUMENT_ROLE_PERMISSIONS, resource_type=ResourceType.LLM_GATEWAY
)


def get_rag_user(
    request: Request,
    current_user: User = Depends(rag_search),
    db: Session = Depends(get_db),
) -> UserContext:
    """Identité construite côté serveur uniquement : jamais depuis le corps de la requête.

    Un utilisateur sans périmètre (département sans couche, voir resolve_scope) est refusé
    avant tout appel au service : 403 générique, événement ACCESS_DENIED /
    DEPARTMENT_FORBIDDEN persisté, sans la question ni le département demandé.
    """
    department = current_user.department.name if current_user.department is not None else None
    user = UserContext(user_id=current_user.id, department=department)
    try:
        resolve_scope(user)
    except PermissionError:
        raise _reject(
            db,
            request,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
            action=AuditAction.ACCESS_DENIED,
            resource_type=ResourceType.LLM_GATEWAY,
            reason=AuditReason.DEPARTMENT_FORBIDDEN,
            user=current_user,
        ) from None
    return user


# ---------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    # Aucun champ d'identité (user_id, rôle, département) n'est accepté du client.
    model_config = ConfigDict(extra="forbid")

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
    try:
        yield
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except InvalidRequest as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RagTimeout as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except RagError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------------------------------------------------
# Audit central (SEC-01) des appels au fournisseur
# ---------------------------------------------------------------------
def _central_audit(db: Session, user: User, request: Request) -> AuditHook:
    """Crochet remis au service : un événement LLM_CALL par compte rendu d'appel.

    ``log_request_calls`` vérifie l'appelant, le request_id et chaque compte rendu avant
    d'écrire (tout ou rien) ; il n'enregistre que des énumérations, des compteurs et le
    request_id tiré par le serveur : ni question, ni prompt, ni extrait, ni réponse.
    """
    ip_address = request.client.host if request.client else None

    def write(context: LLMRequestContext) -> None:
        try:
            log_request_calls(db, user=user, context=context, ip_address=ip_address)
            db.commit()
        except Exception:
            db.rollback()
            raise

    return write


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    service: RagService = Depends(get_rag_service),  # en premier : 503 avant tout
    user: UserContext = Depends(get_rag_user),
    current_user: User = Depends(rag_search),  # même instance que get_rag_user (cache)
    db: Session = Depends(get_db),
):
    audit = _central_audit(db, current_user, request)
    with _map_rag_errors():
        result = await service.ask(user, body.question, body.session_id, audit=audit)
    return result.to_dict()


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    service: RagService = Depends(get_rag_service),  # en premier : 503 avant tout
    user: UserContext = Depends(get_rag_user),
    current_user: User = Depends(rag_search),  # même instance que get_rag_user (cache)
    db: Session = Depends(get_db),
):
    """Événements SSE : {"type": "session"|"token"|"sources"|"done"|"error", ...}

    La session ``db`` (dépendance de portée "request", FastAPI 0.142) reste ouverte jusqu'à
    la fin de l'envoi du flux : l'audit est écrit à la fin de l'agent, dans le flux.
    """
    audit = _central_audit(db, current_user, request)
    events = service.stream(user, body.question, body.session_id, audit=audit)

    # Le 1er événement ("session") n'est émis qu'après les validations (rôle, question,
    # session_id) : on le lit AVANT d'ouvrir le flux pour renvoyer un vrai 403/422.
    with _map_rag_errors():
        first = await anext(events)

    async def event_stream():
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
