"""Routes RAG lorsque l'assistant n'est pas disponible.

Enregistré par ``app.main`` à la place de ``app.api.rag`` quand ``settings.rag_enabled``
est faux, ou quand la pile RAG (groupe de dépendances "rag") ne peut pas être importée :
mêmes chemins, réponse 503, sans authentification, sans base de données et sans aucun
import de LangChain, LlamaIndex, Qdrant ou du fournisseur LLM. Ce module ne doit
importer que FastAPI.
"""

from fastapi import APIRouter, HTTPException, status

RAG_UNAVAILABLE = "Assistant RAG indisponible"

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/chat")
@router.post("/chat/stream")
def rag_unavailable() -> None:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=RAG_UNAVAILABLE)
