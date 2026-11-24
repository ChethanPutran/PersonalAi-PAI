"""Persistent admin history: task/DAG events and finished DAG runs, kept for a number of days.

The live views (Events tab, DAG list) used to hold everything in memory, so a page opened later - or a server
restart - showed nothing. This keeps append-only JSONL files, one per UTC day, under a directory:

    events-YYYY-MM-DD.jsonl   {"ts": <epoch s>, "event": "task.started", "data": {...}}
    dags-YYYY-MM-DD.jsonl     one finished DagSnapshot (JSON) per line

Files older than `retention_days` (the task cleanup window) are deleted by `prune()`. Writes never raise
into the caller: history is a convenience and must not break a run.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Optional

from loguru import logger

# Fields kept when an event payload is too big to store whole (identify the event, drop the bulk).
_KEEP = ("id", "request_id", "task_id", "session_id", "user_id", "name", "capability", "status", "kind", "error",
         "type", "device_id", "executor_id")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


class AdminHistory:
    def __init__(self, directory: str | Path, *, retention_days: int = 7, max_event_bytes: int = 16_384) -> None:
        self.dir = Path(directory)
        self.retention_days = max(1, int(retention_days))
        self.max_event_bytes = max_event_bytes
        self._lock = threading.Lock()
        self._failed = False

    # ------------------------------------------------------------------ write

    def append_event(self, event: str, data: Dict[str, Any], ts: Optional[float] = None) -> None:
        ts = ts if ts is not None else time.time()
        self._append("events", ts, {"ts": ts, "event": str(event), "data": self._bounded(data)})

    def append_dag(self, snapshot: Dict[str, Any], ts: Optional[float] = None) -> None:
        ts = ts if ts is not None else time.time()
        self._append("dags", ts, snapshot)

    def _bounded(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if len(json.dumps(data, default=str)) <= self.max_event_bytes:
                return json.loads(json.dumps(data, default=str))
        except (TypeError, ValueError):
            pass
        kept = {k: data[k] for k in _KEEP if k in data and isinstance(data[k], (str, int, float, bool, type(None)))}
        kept["_truncated"] = True
        return kept

    def _append(self, kind: str, ts: float, record: Dict[str, Any]) -> None:
        if self._failed:
            return
        line = json.dumps(record, default=str, separators=(",", ":"))
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with (self.dir / f"{kind}-{_day(ts)}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as exc:                     # never break a run because history cannot be written
            self._failed = True
            logger.warning("admin history {} is not writable ({}); history will not be kept", self.dir, exc)

    # ------------------------------------------------------------------- read

    def cutoff(self, days: Optional[float] = None, *, now: Optional[float] = None) -> float:
        """Oldest timestamp that is still within retention (and within `days`, if smaller)."""
        span = min(float(days), self.retention_days) if days else float(self.retention_days)
        return (now if now is not None else time.time()) - span * 86400

    def _files(self, kind: str, since: float) -> List[Path]:
        """Files for `kind` that can hold records >= since, oldest first."""
        if not self.dir.exists():
            return []
        first = _day(since)
        return sorted(p for p in self.dir.glob(f"{kind}-*.jsonl") if p.stem[len(kind) + 1:] >= first)

    def _lines(self, kind: str, since: float) -> Iterator[Dict[str, Any]]:
        for path in self._files(kind, since):
            try:
                with path.open(encoding="utf-8") as f:
                    for raw in f:
                        try:
                            yield json.loads(raw)
                        except ValueError:
                            continue                # a half-written last line must not hide the rest
            except OSError:
                continue

    def read_events(self, *, days: Optional[float] = None, since: Optional[float] = None, until: Optional[float] = None,
                    text: str = "", limit: int = 500, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """The newest `limit` events in the window, oldest first (the order the live log shows them)."""
        lo = max(since or 0.0, self.cutoff(days, now=now))
        needle = text.strip().lower()
        out: Deque[Dict[str, Any]] = deque(maxlen=max(1, limit))
        for rec in self._lines("events", lo):
            ts = rec.get("ts")
            if not isinstance(ts, (int, float)) or ts < lo or (until is not None and ts > until):
                continue
            if needle and needle not in json.dumps(rec, default=str).lower():
                continue
            out.append(rec)
        return list(out)

    def read_dags(self, *, days: Optional[float] = None, limit: int = 500, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Finished DAG runs within the window, newest first; one entry per request_id (the last line wins)."""
        lo = self.cutoff(days, now=now)
        latest: Dict[str, Dict[str, Any]] = {}
        for rec in self._lines("dags", lo):
            rid, finished = rec.get("request_id"), self._finished_ts(rec)
            if rid and finished is not None and finished >= lo:
                latest[rid] = rec
        runs = sorted(latest.values(), key=lambda r: self._finished_ts(r) or 0, reverse=True)
        return runs[: max(0, limit)]

    def get_dag(self, request_id: str, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        found = None
        for rec in self._lines("dags", self.cutoff(now=now)):
            if rec.get("request_id") == request_id:
                found = rec
        return found

    @staticmethod
    def _finished_ts(rec: Dict[str, Any]) -> Optional[float]:
        raw = rec.get("finished_at") or rec.get("started_at")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    # ------------------------------------------------------------ retention

    def prune(self, *, now: Optional[float] = None) -> int:
        """Delete day files that are entirely older than the retention window. Returns files removed."""
        if not self.dir.exists():
            return 0
        oldest_kept = _day(self.cutoff(now=now))
        removed = 0
        for path in self.dir.glob("*.jsonl"):
            day = path.stem.split("-", 1)[1] if "-" in path.stem else ""
            if day and day < oldest_kept:
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("could not remove old admin history {}: {}", path, exc)
        return removed

    def info(self) -> Dict[str, Any]:
        files = sorted(self.dir.glob("*.jsonl")) if self.dir.exists() else []
        days = sorted({p.stem.split("-", 1)[1] for p in files})
        return {"retention_days": self.retention_days, "directory": str(self.dir), "days_on_disk": days,
                "oldest_day": days[0] if days else None, "writable": not self._failed}
