"""Controlled integration of the RAG in app.main.

Disabled (default): same /rag paths answering 503, no RAG import, no client. Enabled:
the real router, the service built at startup, any startup failure turned into a 503.
Existing routes and middlewares are preserved in both cases.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app import main
from app.api import rag_unavailable
from app.api.router import api_router
from app.core.config import settings
from app.core.request_timing import RequestTimingMiddleware, elapsed_ms

BACKEND = Path(__file__).resolve().parents[1]
RAG_PATHS = {"/rag/chat", "/rag/chat/stream"}


def _paths(app) -> dict:
    """Exposed operations, from the public OpenAPI schema."""
    return app.openapi()["paths"]


def _rag_handlers(app) -> set[str]:
    """Function behind each RAG path (FastAPI operationId = function name + path)."""
    paths = _paths(app)
    assert RAG_PATHS <= set(paths)
    return {paths[p]["post"]["operationId"].split("_rag_")[0] for p in RAG_PATHS}


def _assert_existing_app_preserved(app):
    reference = FastAPI()
    reference.include_router(api_router)
    paths = _paths(app)
    for path, operations in _paths(reference).items():
        assert set(operations) <= set(paths[path]), path
    assert "/health" in paths
    # Same middlewares, same order: RequestTimingMiddleware stays the outermost one.
    assert [m.cls for m in app.user_middleware] == [RequestTimingMiddleware, CORSMiddleware]


@pytest.fixture
def rag_enabled(monkeypatch):
    pytest.importorskip("langchain_classic")
    monkeypatch.setattr(settings, "rag_enabled", True)
    from app.api import rag as rag_api

    yield rag_api
    rag_api.close_rag_service()


# --- RAG disabled (default) -----------------------------------------------------------


def test_the_rag_is_disabled_by_default():
    assert settings.rag_enabled is False
    assert main.app.state.rag is None


def test_disabled_registers_the_rag_paths_answering_503():
    assert _rag_handlers(main.app) == {"rag_unavailable"}
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for path in RAG_PATHS:  # no token, no body: nothing is checked, nothing is written
            response = client.post(path)
            assert response.status_code == 503
            assert response.json() == {"detail": rag_unavailable.RAG_UNAVAILABLE}


def test_disabled_keeps_existing_routes_and_middlewares():
    _assert_existing_app_preserved(main.app)


def test_request_timing_still_runs_with_the_rag_routes():
    app = main.create_app()
    app.add_api_route("/probe", lambda: {"elapsed": elapsed_ms()})
    with TestClient(app) as client:
        assert client.get("/probe").json()["elapsed"] is not None


def test_disabled_startup_loads_no_rag_stack_and_no_client():
    """In a fresh interpreter: import app.main, start it, call the routes."""
    script = """
import json, sys
from fastapi.testclient import TestClient
import app.main as main
with TestClient(main.app) as client:
    health = client.get("/health").status_code
    rag = client.post("/rag/chat").status_code
heavy = ["torch", "langchain", "langchain_core", "langgraph", "llama_index", "qdrant_client",
         "groq", "sentence_transformers"]
print(json.dumps({
    "health": health,
    "rag": rag,
    "rag_modules": sorted(m for m in sys.modules if m.startswith("app.services.Agentic_RAG")),
    "rag_route_module": "app.api.rag" in sys.modules,
    "heavy": sorted(m for m in heavy if m in sys.modules),
}))
"""
    env = {**os.environ, "RAG_ENABLED": "false", "SEED_ON_STARTUP": "false"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {
        "health": 200,
        "rag": 503,
        "rag_modules": [],
        "rag_route_module": False,
        "heavy": [],
    }


# --- RAG enabled ----------------------------------------------------------------------


def test_enabled_registers_the_real_router_and_builds_the_service(rag_enabled, monkeypatch):
    built = object()
    calls = []

    async def create(cls, **kwargs):
        calls.append(kwargs)
        return built

    monkeypatch.setattr(rag_enabled.RagService, "create", classmethod(create))
    app = main.create_app()
    assert app.state.rag is rag_enabled
    assert _rag_handlers(app) == {"chat", "chat_stream"}  # the real app.api.rag routes
    _assert_existing_app_preserved(app)

    with TestClient(app) as client:
        assert calls == [{}]  # built once, at startup
        assert rag_enabled.get_rag_service() is built
        assert client.get("/health").status_code == 200
    with pytest.raises(Exception) as stopped:  # released at shutdown
        rag_enabled.get_rag_service()
    assert stopped.value.status_code == 503


def test_an_initialisation_failure_keeps_the_api_up_and_answers_503(
    rag_enabled, monkeypatch, caplog
):
    async def create(cls, **kwargs):
        raise RuntimeError("Qdrant https://secret-host.example key=SECRET-KEY unreachable")

    monkeypatch.setattr(rag_enabled.RagService, "create", classmethod(create))
    caplog.set_level(logging.ERROR, logger="app.main")
    app = main.create_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        for path in RAG_PATHS:
            # 503 before authentication: no token needed, no refusal is audited
            response = client.post(path, json={"question": "q"})
            assert response.status_code == 503
            assert response.json() == {"detail": rag_unavailable.RAG_UNAVAILABLE}
    assert "RAG initialisation failed: RuntimeError" in caplog.text
    assert "secret-host" not in caplog.text and "SECRET-KEY" not in caplog.text


def test_enabled_without_the_rag_dependencies_falls_back_to_503(monkeypatch, caplog):
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setitem(sys.modules, main.RAG_MODULE, None)  # import -> ImportError
    caplog.set_level(logging.ERROR, logger="app.main")
    app = main.create_app()
    assert app.state.rag is None
    assert _rag_handlers(app) == {"rag_unavailable"}
    _assert_existing_app_preserved(app)
    with TestClient(app) as client:
        assert client.post("/rag/chat").status_code == 503
    assert "RAG enabled but not importable: ModuleNotFoundError" in caplog.text
