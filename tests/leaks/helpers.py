"""Shared helpers for the leak / shutdown tests."""
import asyncio
import gc
import os
import threading
from types import SimpleNamespace
from typing import List, Set


class FakeWebSocket:
    """In-process stand-in for the server side of a Starlette WebSocket."""

    def __init__(self, app, query: dict):
        self.app = app
        self.query_params = query
        self._inbox: "asyncio.Queue[dict]" = asyncio.Queue()
        self.sent_text: List[str] = []
        self.sent_bytes = 0
        self.closed = asyncio.Event()
        self.close_code = None

    async def accept(self): ...
    async def send_text(self, t): self.sent_text.append(t)
    async def send_bytes(self, b): self.sent_bytes += len(b)
    async def send_json(self, d): self.sent_text.append(str(d))

    async def receive(self):
        return await self._inbox.get()

    async def close(self, code: int = 1000):
        self.close_code = code
        self.closed.set()
        self._inbox.put_nowait({"type": "websocket.disconnect", "code": code})

    def client_sends_text(self, text: str):
        self._inbox.put_nowait({"type": "websocket.receive", "text": text})

    def client_sends_audio(self, pcm: bytes):
        self._inbox.put_nowait({"type": "websocket.receive", "bytes": pcm})


def thread_snapshot() -> Set[str]:
    return {f"{t.name}|{t.daemon}" for t in threading.enumerate()}


def open_fd_targets() -> List[str]:
    out = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            out.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            pass
    return out


def leftover_tasks(me=None) -> List[asyncio.Task]:
    me = me or asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not me and not t.done()]


async def settle(seconds: float = 0.3):
    await asyncio.sleep(seconds)
    gc.collect()


def isolated_config(tmp_path):
    """The real Config, with every writable path redirected into ``tmp_path``.

    NOTE: environment variables do NOT override config (non-secret settings come only from
    the YAML on purpose), so isolation has to be done on the Config object itself.
    """
    from pai.config.loader import load_config
    c = load_config()
    c.database.url = f"sqlite+aiosqlite:///{tmp_path / 'pai.db'}"
    c.memory.long_term.db_url = f"sqlite+aiosqlite:///{tmp_path / 'lt.db'}"
    c.memory.episodic.db_url = f"sqlite+aiosqlite:///{tmp_path / 'ep.db'}"
    c.memory.vector_store.persist_directory = str(tmp_path / "chroma")
    c.memory.procedural.path = str(tmp_path / "proc.json")
    c.tracing.persist_dir = str(tmp_path / "traces")
    c.logging.log_dir = tmp_path / "logs"
    # No model downloads / real engines in tests: voice uses the fakes unless a test replaces it.
    from pai.config.models.voice import STTProviderName, TTSProviderName
    c.voice.stt, c.voice.tts = STTProviderName.FAKE, TTSProviderName.FAKE
    c.voice.models_dir = str(tmp_path / "voice_models")
    c.voice.auto_download_models = False
    return c
