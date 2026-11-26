"""Shared helpers for the CLI tests: a real APIClient over httpx.MockTransport, a route table, a Ctx."""
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from pai_device.cli_support import Ctx
from pai_device.config import DeviceConfig
from pai_device.transport import APIClient


class Routes:
    """``routes.add("GET", "/plugins/operations/op1", json=[...])``: a list is consumed one reply per call
    (the last one repeats). Paths are relative to /api/v1. Every request is recorded in ``calls``."""

    def __init__(self) -> None:
        self.table: Dict[Tuple[str, str], List[Any]] = {}
        self.calls: List[httpx.Request] = []

    def add(self, method: str, path: str, *, json_body: Any = None, status: int = 200, text: Optional[str] = None,
            sse: Optional[str] = None, replies: Optional[List[Any]] = None) -> None:
        reps = replies if replies is not None else [dict(json=json_body, status=status, text=text, sse=sse)]
        self.table[(method, path)] = list(reps)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path.removeprefix("/api/v1")
        key = (req.method, path)
        if key not in self.table:
            return httpx.Response(404, json={"detail": f"no route {key}"})
        reps = self.table[key]
        rep = reps.pop(0) if len(reps) > 1 else reps[0]
        if rep.get("sse") is not None:
            return httpx.Response(rep.get("status", 200), text=rep["sse"],
                                  headers={"content-type": "text/event-stream"})
        if rep.get("text") is not None:
            return httpx.Response(rep.get("status", 200), text=rep["text"])
        return httpx.Response(rep.get("status", 200), json=rep.get("json"))

    def bodies(self, method: str, path: str) -> List[Any]:
        out = []
        for r in self.calls:
            if r.method == method and r.url.path.removeprefix("/api/v1") == path:
                out.append(json.loads(r.content) if r.content else None)
        return out

    def count(self, method: str, path: str) -> int:
        return len(self.bodies(method, path))


def make_client(handler: Callable[[httpx.Request], httpx.Response], device_id: Optional[str] = "dev-1") -> APIClient:
    c = APIClient("http://t/api/v1", "me@example.com", "secret-pw")
    c._http = httpx.AsyncClient(base_url="http://t/api/v1", transport=httpx.MockTransport(handler))

    async def _tok():
        c._token = "tok"
        return "tok"
    c.ensure_token = _tok
    c.device_id = device_id
    return c


def make_ctx(client: APIClient, tmp_path: Path, device_id: Optional[str] = "dev-1") -> Ctx:
    cfg = DeviceConfig(backend_url="http://backend:8000", email="me@example.com", password="secret-pw",
                       device_name="laptop", state_dir=tmp_path / "state", plugin_dir=tmp_path / "plugins")
    return Ctx(client=client, device_id=device_id, config=cfg)


def sse_text(*events: Tuple[str, Any]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
