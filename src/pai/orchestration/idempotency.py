"""Goal-level idempotency (``Idempotency-Key`` on POST /chat).

Contract, per (user_id, session_id, key):

  * first caller           -> OWNER: runs the work, then ``set_result`` / lets the
                              guard exit.
  * same key while running -> JOINER: waits for the owner's result (no second run).
  * same key after success -> REPLAY: the cached result, for ``success_ttl_seconds``.
  * a *failed* outcome     -> cached only ``failure_ttl_seconds`` (short), so the
                              client can retry soon but a tight retry loop can't
                              hammer the backend.
  * owner raised/cancelled -> nothing cached; joiners get ``IdempotentRunAborted``
                              (the original attempt was interrupted — safe to retry).

Different users or sessions never collide: both are part of the key.
"""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, AsyncIterator, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

_KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


class IdempotentRunAborted(RuntimeError):
    """The request that owned this key was interrupted before producing a result."""


class IdempotencyPolicy(BaseModel):
    success_ttl_seconds: float = 600.0
    failure_ttl_seconds: float = 5.0
    max_entries: int = 1000


class IdempotencyKey(BaseModel):
    """Scoped key. Validated so a header can't smuggle odd strings into logs/ids."""
    model_config = ConfigDict(frozen=True)

    user_id: str
    session_id: str = ""        # "" when the client didn't pin a session
    key: str

    @field_validator("key")
    @classmethod
    def _valid_key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("Idempotency-Key must be 1-128 chars of [A-Za-z0-9._:-]")
        return v

    @classmethod
    def from_header(cls, value: Optional[str], *, user_id: str, session_id: Optional[str]) -> Optional["IdempotencyKey"]:
        value = (value or "").strip()
        return cls(user_id=user_id, session_id=session_id or "", key=value) if value else None


class Role(StrEnum):
    OWNER = "owner"
    JOINER = "joiner"      # waited for an in-flight owner
    REPLAY = "replay"      # served from the completed-result cache


class _Entry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    future: asyncio.Future
    expires_at: Optional[float] = None      # None while in flight
    created_at: float = Field(default_factory=time.monotonic)


class Guard(BaseModel, Generic[T]):
    """What ``IdempotencyRegistry.guard`` yields."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: Role
    replay: Optional[Any] = None            # set for JOINER / REPLAY
    _owner_entry: Optional[_Entry] = None
    _registry: Optional[Any] = None
    _key: Optional[IdempotencyKey] = None
    _finished: bool = False

    @property
    def is_replay(self) -> bool:
        return self.role != Role.OWNER

    def set_result(self, value: T, *, success: bool = True) -> None:
        """Owner only: publish the outcome to joiners and the cache."""
        if self.role != Role.OWNER or self._finished or self._registry is None:
            return          # replay, already published, or the no-key no-op guard
        self._finished = True
        self._registry._complete(self._key, self._owner_entry, value, success)


class IdempotencyRegistry(Generic[T]):
    """In-process registry. (Single-process only; swap the storage for Redis to share
    across workers — the guard() contract stays the same.)"""

    def __init__(self, policy: Optional[IdempotencyPolicy] = None) -> None:
        self.policy = policy or IdempotencyPolicy()
        self._entries: dict[IdempotencyKey, _Entry] = {}

    # ---- public ----------------------------------------------------------

    @asynccontextmanager
    async def guard(self, key: Optional[IdempotencyKey]) -> AsyncIterator[Guard[T]]:
        """``async with registry.guard(key) as g: if g.is_replay: return g.replay; ...``

        ``key=None`` (no header) is a no-op owner guard: behaviour is unchanged.
        """
        if key is None:
            yield Guard(role=Role.OWNER)
            return

        self._purge()
        existing = self._entries.get(key)
        if existing is not None:
            if existing.future.done():
                yield Guard(role=Role.REPLAY, replay=existing.future.result())
                return
            # in flight: join. shield so a cancelled joiner doesn't cancel the owner's future.
            value = await asyncio.shield(existing.future)
            yield Guard(role=Role.JOINER, replay=value)
            return

        entry = _Entry(future=asyncio.get_running_loop().create_future())
        self._entries[key] = entry
        guard: Guard[T] = Guard(role=Role.OWNER)
        guard._owner_entry, guard._registry, guard._key = entry, self, key
        try:
            yield guard
        except BaseException as exc:          # includes CancelledError (client disconnect)
            self._abort(key, entry, exc)
            raise
        else:
            if not guard._finished:           # owner never published: don't leave joiners hanging
                self._abort(key, entry, IdempotentRunAborted("run finished without a result"))

    # ---- internals -------------------------------------------------------

    def _complete(self, key: IdempotencyKey, entry: _Entry, value: Any, success: bool) -> None:
        ttl = self.policy.success_ttl_seconds if success else self.policy.failure_ttl_seconds
        entry.expires_at = time.monotonic() + ttl
        if not entry.future.done():
            entry.future.set_result(value)

    def _abort(self, key: IdempotencyKey, entry: _Entry, cause: BaseException) -> None:
        if self._entries.get(key) is entry:
            del self._entries[key]
        if not entry.future.done():
            entry.future.set_exception(IdempotentRunAborted(str(cause) or type(cause).__name__))
            entry.future.exception()          # mark retrieved: no "never retrieved" warning if nobody joined

    def _purge(self) -> None:
        now = time.monotonic()
        for k in [k for k, e in self._entries.items() if e.expires_at is not None and e.expires_at <= now]:
            del self._entries[k]
        overflow = len(self._entries) - self.policy.max_entries
        if overflow > 0:
            done = sorted((e.created_at, k) for k, e in self._entries.items() if e.future.done())
            for _, k in done[:overflow]:
                del self._entries[k]
