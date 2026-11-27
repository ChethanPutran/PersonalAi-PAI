"""Keeps the backend's document index of this device in step with the files plugin's folders.

One :meth:`DocumentIndexer.run_once` = walk (in a low-priority thread), upload only what changed, remove what
went away or is no longer allowed, then update the local state. ``indexDocuments`` off = clear everything.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

import httpx

from ..path_policy import is_within
from .extract import PDF_INSTALL_HINT, pdf_available
from .scanner import Item, Scanner
from .state import Entry, IndexState

logger = logging.getLogger(__name__)

BATCH_CHUNKS = 400
REMOVE_BATCH = 200
RETRY_DELAYS = (2.0, 5.0, 15.0, 30.0)
POLICY_KEYS = ("allowedPaths", "deniedPaths", "indexDocuments", "indexMaxFileMb")


class IndexStopped(Exception):
    """The run cannot continue (indexing is off on the server, a hard error, the server stayed unavailable)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def enabled(values: Optional[Mapping[str, Any]]) -> bool:
    return bool(values) and values.get("indexDocuments") is True


def policy_changed(old: Optional[Mapping[str, Any]], new: Optional[Mapping[str, Any]]) -> bool:
    old, new = old or {}, new or {}
    return any(old.get(k) != new.get(k) for k in POLICY_KEYS)


@dataclass
class Report:
    enabled: bool = True
    files_seen: int = 0
    files_sent: int = 0
    chunks_sent: int = 0
    files_unchanged: int = 0
    files_removed: int = 0
    truncated: int = 0
    skipped: Counter = field(default_factory=Counter)
    errors: List[str] = field(default_factory=list)
    stopped: Optional[str] = None            # code of the reason the run ended early
    message: str = ""
    hint: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["skipped"] = dict(self.skipped)
        return d


def _lower_priority() -> None:
    try:
        os.nice(10)
    except Exception:
        pass


def _detail_code(exc: httpx.HTTPStatusError) -> tuple[str, str]:
    try:
        d = exc.response.json().get("detail")
    except Exception:
        d = None
    if isinstance(d, dict):
        return str(d.get("code") or ""), str(d.get("message") or "")
    return "", str(d or "")


class DocumentIndexer:
    def __init__(self, api: Any, device_id: str, state_dir: Path, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 per_file_seconds: float = 60.0, yield_seconds: float = 0.02) -> None:
        self.api, self.device_id = api, device_id
        self.state = IndexState(state_dir)
        self._sleep = sleep
        self._per_file_seconds = per_file_seconds
        self._yield = yield_seconds
        self._lock = asyncio.Lock()
        self._warned_pdf = False

    # -- server calls with back-off ---------------------------------------------------------------
    async def _call(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return await self.api.request_json(method, path, json=json, params=params)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                code, message = _detail_code(exc)
                if status == 409 and code == "indexing_off":
                    raise IndexStopped("indexing_off", message or "Indexing is off for this device") from exc
                if status == 429 or status >= 500:
                    if attempt < len(RETRY_DELAYS):
                        try:
                            wait = float(exc.response.headers.get("retry-after", ""))
                        except ValueError:
                            wait = RETRY_DELAYS[attempt]
                        await self._sleep(min(max(wait, RETRY_DELAYS[attempt]), 60.0))
                        continue
                    raise IndexStopped("server_busy", f"The server kept answering {status}; will retry later") from exc
                raise IndexStopped(code or f"http_{status}", message or f"The server refused the request ({status})") from exc
            except (httpx.TransportError, OSError) as exc:
                if attempt < len(RETRY_DELAYS):
                    await self._sleep(RETRY_DELAYS[attempt])
                    continue
                raise IndexStopped("offline", f"The server is not reachable ({type(exc).__name__})") from exc
        raise IndexStopped("server_busy", "gave up")                   # pragma: no cover

    async def server_files(self) -> Dict[str, Dict[str, Any]]:
        data = await self._call("GET", "/documents/files", params={"device_id": self.device_id})
        files = (data or {}).get("files") or {}
        return files if isinstance(files, dict) else {}

    async def server_status(self) -> Dict[str, Any]:
        return await self._call("GET", "/documents/status", params={"device_id": self.device_id}) or {}

    async def _remove(self, paths: Optional[List[str]] = None) -> None:
        if paths is None:
            await self._call("POST", "/documents/remove", json={"device_id": self.device_id})
            return
        for i in range(0, len(paths), REMOVE_BATCH):
            await self._call("POST", "/documents/remove",
                             json={"device_id": self.device_id, "paths": paths[i:i + REMOVE_BATCH]})

    # -- clearing ---------------------------------------------------------------------------------
    async def clear(self) -> None:
        """Delete this device's chunks on the server and the local state."""
        async with self._lock:
            await self._remove(None)
            self.state.clear()

    async def apply(self, values: Optional[Mapping[str, Any]], *, progress: Optional[Callable[[Report], None]] = None,
                    force_clear: bool = False) -> Report:
        """The one entry point for the agent: index when enabled, otherwise remove what was indexed."""
        if enabled(values):
            return await self.run_once(dict(values or {}), progress=progress)
        report = Report(enabled=False)
        if force_clear or self.state.count():
            try:
                await self.clear()
                report.message = "Indexing is off: this device's indexed documents were removed."
            except IndexStopped as exc:
                report.stopped, report.message = exc.code, exc.message
        return report

    # -- the run ----------------------------------------------------------------------------------
    async def run_once(self, values: Dict[str, Any], *, progress: Optional[Callable[[Report], None]] = None) -> Report:
        async with self._lock:
            report = Report()
            try:
                await self._run(values, report, progress or (lambda r: None))
            except IndexStopped as exc:
                report.stopped, report.message = exc.code, exc.message
                if exc.code == "indexing_off":
                    report.hint = "Turn on 'Index documents' in this device's Files plugin settings."
                logger.warning("Document indexing stopped (%s): %s", exc.code, exc.message)
            return report

    async def _run(self, values: Dict[str, Any], report: Report, progress: Callable[[Report], None]) -> None:
        server = await self.server_files()
        local = self.state.all()
        scanner = Scanner(values, local, server, per_file_seconds=self._per_file_seconds)
        eligible: set = set()
        pending: List[Item] = []
        pending_chunks = 0
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pai-index", initializer=_lower_priority)
        it = scanner.items()
        done = object()
        try:
            while True:
                item = await loop.run_in_executor(pool, next, it, done)
                if item is done:
                    break
                report.files_seen += 1
                if item.kind == "skip":
                    report.skipped[item.reason] += 1
                    if item.reason == "pdf_unsupported" and not self._warned_pdf:
                        self._warned_pdf = True
                        report.hint = PDF_INSTALL_HINT
                        logger.warning(PDF_INSTALL_HINT)
                elif item.kind == "unchanged":
                    eligible.add(item.path)
                    report.files_unchanged += 1
                elif item.kind == "empty":
                    eligible.add(item.path)
                    report.skipped["empty"] += 1
                    self.state.put(item.path, Entry(item.mtime, item.size, "", 0))
                    if item.path in server:                    # it had text once, it has none now
                        await self._remove([item.path])
                else:
                    eligible.add(item.path)
                    if pending and pending_chunks + len(item.chunks) > BATCH_CHUNKS:
                        await self._flush(pending, report)
                        pending, pending_chunks = [], 0
                    pending.append(item)
                    pending_chunks += len(item.chunks)
                    if item.truncated:
                        report.truncated += 1
                progress(report)
                if self._yield:
                    await self._sleep(self._yield)
            if pending:
                await self._flush(pending, report)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        report.skipped.update(scanner.stats)
        # Whole walk finished: what is on the server/state but no longer eligible (deleted, now denied, folder
        # removed, too big now) goes. A folder that is merely missing right now (unmounted) is left alone.
        gone = sorted((set(server) | set(local)) - eligible)
        gone = [p for p in gone if not any(is_within(p, r) for r in scanner.missing_roots)]
        if gone:
            await self._remove([p for p in gone if p in server])
            self.state.delete(gone)
            report.files_removed = len(gone)
        progress(report)

    async def _flush(self, batch: List[Item], report: Report) -> None:
        body = {"device_id": self.device_id,
                "files": [{"path": i.path, "mtime": i.mtime, "size": i.size, "chunks": i.chunks} for i in batch]}
        await self._call("POST", "/documents/index", json=body)
        for i in batch:
            self.state.put(i.path, Entry(i.mtime, i.size, i.digest, len(i.chunks)))
            report.files_sent += 1
            report.chunks_sent += len(i.chunks)
