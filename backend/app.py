from __future__ import annotations

from typing import Any, Dict

from src.core.kernel import build_kernel

try:
    from fastapi import FastAPI
except Exception:  # pragma: no cover
    FastAPI = None


def create_app():
    kernel = build_kernel()

    if FastAPI is None:
        return kernel

    app = FastAPI(title="Personal AI", version="2.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/plugins")
    def plugins():
        return {"plugins": kernel.list_plugins()}

    @app.post("/chat")
    def chat(payload: Dict[str, Any]):
        message = str(payload.get("message", ""))
        return {"response": kernel.handle_message(message), "plugins": kernel.list_plugins()}

    @app.get("/memory")
    def memory():
        return kernel.snapshot()

    @app.post("/memory/facts")
    def add_fact(payload: Dict[str, Any]):
        fact = str(payload.get("fact", ""))
        tags = list(payload.get("tags", []))
        kernel.memory.add_fact(fact, tags)
        return {"status": "stored", "fact": fact, "tags": tags}

    @app.get("/memory/search")
    def search_memory(query: str):
        return {"query": query, "results": kernel.memory.search(query)}

    return app
