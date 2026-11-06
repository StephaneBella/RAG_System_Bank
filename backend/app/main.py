import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rag import init_rag_service  # [RAG] ajout
from app.api.rag import router as rag_router
from app.api.router import api_router
from app.core.config import settings
from app.db.seed import seed_initial_data

logger = logging.getLogger(__name__)


async def _seed_database() -> None:
    try:
        await asyncio.to_thread(seed_initial_data)
    except Exception:
        logger.exception("Initial data seeding failed")


async def _warm_rag() -> None:
    # Yield once so Uvicorn can bind the HTTP port before heavy RAG imports start.
    await asyncio.sleep(0)
    try:
        await init_rag_service()
    except Exception:
        logger.exception("RAG initialization failed; /rag endpoints will return 503")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(_seed_database()), asyncio.create_task(_warm_rag())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(
    title="Bank RAG Document Sprint 2",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(rag_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
