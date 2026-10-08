from fastapi import FastAPI

app = FastAPI(
    title="Bank RAG Document Sprint 1",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}
