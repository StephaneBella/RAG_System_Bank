from fastapi import FastAPI

from app.api.router import api_router

app = FastAPI(
    title="Bank RAG Document Sprint 1",
)

app.include_router(api_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
