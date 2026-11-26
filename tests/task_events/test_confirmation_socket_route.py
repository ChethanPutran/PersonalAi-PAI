"""The approval prompt reaches the app over `WS /tasks/{session_id}/stream`.

The Flutter ConfirmationService once opened `WS /tasks/stream?session_id=...`, which is not a WebSocket route:
the socket was closed at once, so a paused task waited for an approval the user never saw.
"""
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

from pai.api.routes import tasks


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api/v1")
    return app


def test_the_session_websocket_exists_where_the_app_connects():
    paths = {r.path for r in _app().routes if isinstance(r, APIWebSocketRoute)}
    assert "/api/v1/tasks/{session_id}/stream" in paths


def test_tasks_stream_is_not_a_websocket_route():
    # `/tasks/stream` only matches GET /tasks/{task_id} (task_id="stream"); the SSE feed is /tasks/events.
    app = _app()
    assert "/api/v1/tasks/stream" not in {r.path for r in app.routes if isinstance(r, APIWebSocketRoute)}
    assert any(isinstance(r, APIRoute) and r.path == "/api/v1/tasks/events" for r in app.routes)
