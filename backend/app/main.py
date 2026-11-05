import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import rag_unavailable
from app.api.router import api_router
from app.core.config import settings
from app.core.request_timing import RequestTimingMiddleware
from app.db.seed import seed_initial_data

logger = logging.getLogger(__name__)

RAG_MODULE = "app.api.rag"


def _load_rag() -> ModuleType | None:
    """The RAG route module, imported only when ``settings.rag_enabled`` is true.

    Importing it loads LangChain, LlamaIndex, Qdrant and torch ("rag" dependency group):
    never when the RAG is disabled. The import itself creates no Qdrant, embedding or LLM
    client; they are built at startup by ``init_rag_service``. If the "rag" group is not
    installed, the API still starts and the RAG routes answer 503.
    """
    if not settings.rag_enabled:
        return None
    try:
        return importlib.import_module(RAG_MODULE)
    except ImportError as error:
        logger.error("RAG enabled but not importable: %s", type(error).__name__)
        return None


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    seed_initial_data()
    rag = application.state.rag
    if rag is not None:
        try:
            await rag.init_rag_service()
        except Exception as error:  # noqa: BLE001 - the API starts, the RAG answers 503
            # Type only: a Qdrant or provider error may carry a URL or a key.
            logger.error("RAG initialisation failed: %s", type(error).__name__)
    try:
        yield
    finally:
        if rag is not None:
            rag.close_rag_service()


def health_check():
    return {"status": "ok"}


def create_app() -> FastAPI:
    application = FastAPI(
        title="Bank RAG Document Sprint 1",
        lifespan=lifespan,
    )
    application.state.rag = _load_rag()

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestTimingMiddleware)

    application.include_router(api_router)
    rag = application.state.rag
    application.include_router(rag.router if rag is not None else rag_unavailable.router)
    application.add_api_route("/health", health_check, methods=["GET"])
    return application


app = create_app()
