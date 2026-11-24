"""Long-lived client streams must end promptly when the server shuts down."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from pai.api.routes import chat as chat_routes
from pai.api.routes import tasks as task_routes
from pai.config.models.event_bus import EventBusConfig
from pai.event_bus.bus import EventBus


class _DB:
    def session(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *a): ...
    async def execute(self, stmt): return self
    def scalar_one_or_none(self): return None


class _ClientSocket:
    """The client side stays connected and silent for the whole test."""
    def __init__(self, context):
        self.query_params = {"token": "t"}
        self.app = SimpleNamespace(state=SimpleNamespace(context=context))
        self.sent = []
        self._forever = asyncio.Event()
    async def accept(self): ...
    async def send_json(self, m): self.sent.append(m)
    async def close(self, code=1000): ...
    async def receive_text(self):
        await self._forever.wait()
        raise WebSocketDisconnect()


@pytest.mark.parametrize("module", [task_routes, chat_routes], ids=["tasks_ws", "chat_ws"])
async def test_status_websocket_ends_when_server_shuts_down(module, monkeypatch):
    monkeypatch.setattr(module, "decode_access_token", lambda t: "u")
    bus = EventBus(config=EventBusConfig())
    await bus.initialize(); await bus.start()
    ctx = SimpleNamespace(database=_DB(), orchestrator=SimpleNamespace(event_bus=bus),
                          shutting_down=asyncio.Event())
    ws = _ClientSocket(ctx)
    route = asyncio.create_task(module.chat_status_stream(ws, "s1"))
    await asyncio.sleep(0.2)
    assert not route.done()                                       # connected and idle
    assert sum(len(v) for v in bus._subscriptions.values()) > 0

    ctx.shutting_down.set()                                       # the server begins to stop
    await asyncio.wait_for(route, 2.0)                            # ...and the handler returns by itself
    assert sum(len(v) for v in bus._subscriptions.values()) == 0  # with its subscriptions released
    await bus.shutdown()
