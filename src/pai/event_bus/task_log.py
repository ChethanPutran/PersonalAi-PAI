"""Per-user task event log: the source of truth behind ``GET /tasks/events``.

Every task status change is appended to the OWNER's log with a per-user, strictly increasing
sequence number (``seq``). A client that drops and reconnects sends ``Last-Event-ID: <seq>``
and gets exactly the events it missed — so the app needs no polling and never goes stale.

Two backends behind one interface:

* :class:`InMemoryTaskEventLog` (default) — a bounded ring buffer per user. Right for a single
  backend instance. The sequence space resets when the process restarts; a client that
  reconnects with a number from the old space is told to ``resync`` (reload once).
* :class:`JetStreamTaskEventLog` (optional) — NATS JetStream. Events survive restarts and are
  shared by several backend instances. See the class docstring for how ``seq`` and gap
  detection work there.

Everything is bounded: events per user, users tracked, subscriber queues. A slow consumer is
never allowed to grow memory; it is flagged for ``resync`` instead.
"""
from __future__ import annotations

import abc
import asyncio
import json
import time
from collections import OrderedDict, deque
from typing import Any, Deque, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from pai.config.models.task_events import JetStreamLogConfig, TaskEventsConfig


# ----------------------------------------------------------------------------- models

class UserTaskEvent(BaseModel):
    """One entry of a user's task event log."""
    seq: int                                   # per-user, strictly increasing
    user_id: str
    event: str                                 # e.g. "task.started", "dag.completed"
    data: Dict[str, Any] = Field(default_factory=dict)   # the event payload (JSON object by nature)
    ts: float = Field(default_factory=time.time)


class ReplayResult(BaseModel):
    """What a reconnecting client is owed."""
    events: List[UserTaskEvent] = Field(default_factory=list)
    gap: bool = False          # True: ``after`` is unknown/too old — events were lost, client must reload
    latest_seq: int = 0


class LogSubscription(abc.ABC):
    """A live feed of ONE user's events. Always ``close()`` it."""

    needs_resync: bool = False     # True after the consumer fell behind: events were dropped

    @abc.abstractmethod
    async def get(self, timeout: float) -> Optional[UserTaskEvent]:
        """Next event, or None after ``timeout`` seconds (or when closed)."""

    @abc.abstractmethod
    async def close(self) -> None: ...

    def clear_resync(self) -> None:
        self.needs_resync = False


class TaskEventLog(abc.ABC):
    """Append / replay / subscribe, scoped per user."""

    async def start(self) -> None:           # connect (JetStream); no-op for memory
        return None

    @abc.abstractmethod
    async def append(self, user_id: str, event: str, data: Dict[str, Any]) -> int:
        """Record an event for ``user_id``; returns its seq."""

    @abc.abstractmethod
    async def replay(self, user_id: str, after_seq: int) -> ReplayResult:
        """Events with seq > ``after_seq`` (oldest first), or ``gap=True`` if they are gone."""

    @abc.abstractmethod
    async def subscribe(self, user_id: str) -> LogSubscription: ...

    @abc.abstractmethod
    async def latest_seq(self, user_id: str) -> int: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @property
    def name(self) -> str:
        return type(self).__name__


# --------------------------------------------------------------------------- in-memory

class _MemorySubscription(LogSubscription):
    def __init__(self, owner: "InMemoryTaskEventLog", user_id: str, maxsize: int) -> None:
        self._owner, self._user_id = owner, user_id
        self._queue: "asyncio.Queue[Optional[UserTaskEvent]]" = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def offer(self, event: UserTaskEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Never grow and never silently skip: drop everything queued and flag a resync.
            # The client reloads its list once, which is cheaper than a half-true stream.
            self.needs_resync = True
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def get(self, timeout: float) -> Optional[UserTaskEvent]:
        if self._closed and self._queue.empty():
            return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._owner._drop_subscription(self._user_id, self)
        with_wakeup = self._queue.empty()
        if with_wakeup:                      # wake a consumer blocked in get()
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass


class _UserBuffer:
    __slots__ = ("seq", "events", "subs")

    def __init__(self, size: int) -> None:
        self.seq = 0
        self.events: Deque[UserTaskEvent] = deque(maxlen=size)
        self.subs: set[_MemorySubscription] = set()


class InMemoryTaskEventLog(TaskEventLog):
    """Bounded per-user ring buffers. Single process; the default."""

    def __init__(
        self,
        *,
        buffer_size: int = 1000,
        max_users: int = 10_000,
        ttl_seconds: Optional[float] = None,
        subscriber_queue: int = 256,
    ) -> None:
        self._size = buffer_size
        self._max_users = max_users
        self._ttl = ttl_seconds
        self._queue_size = subscriber_queue
        self._users: "OrderedDict[str, _UserBuffer]" = OrderedDict()     # LRU: oldest first
        self._lock = asyncio.Lock()
        self._closed = False

    # ---------------------------------------------------------------------- internals
    def _buf(self, user_id: str) -> _UserBuffer:
        buf = self._users.get(user_id)
        if buf is None:
            buf = self._users[user_id] = _UserBuffer(self._size)
            self._evict_if_needed()
        else:
            self._users.move_to_end(user_id)
        return buf

    def _evict_if_needed(self) -> None:
        """Keep the number of tracked users bounded. Users with a live subscriber are kept."""
        if len(self._users) <= self._max_users:
            return
        for uid in list(self._users):
            if len(self._users) <= self._max_users:
                break
            if not self._users[uid].subs:
                del self._users[uid]          # its seq space resets: a returning client gets `resync`

    def _drop_subscription(self, user_id: str, sub: _MemorySubscription) -> None:
        buf = self._users.get(user_id)
        if buf is not None:
            buf.subs.discard(sub)

    def _live(self, buf: _UserBuffer) -> List[UserTaskEvent]:
        if self._ttl is None:
            return list(buf.events)
        cutoff = time.time() - self._ttl
        return [e for e in buf.events if e.ts >= cutoff]

    # ------------------------------------------------------------------------ interface
    async def append(self, user_id: str, event: str, data: Dict[str, Any]) -> int:
        async with self._lock:
            if self._closed:
                return 0
            buf = self._buf(user_id)
            buf.seq += 1
            entry = UserTaskEvent(seq=buf.seq, user_id=user_id, event=event, data=data)
            buf.events.append(entry)
            for sub in list(buf.subs):
                sub.offer(entry)
            return buf.seq

    async def replay(self, user_id: str, after_seq: int) -> ReplayResult:
        async with self._lock:
            buf = self._users.get(user_id)
            latest = buf.seq if buf else 0
            if buf is None:
                # Unknown user: nothing buffered. A client that remembers a seq lost it (restart).
                return ReplayResult(gap=after_seq > 0, latest_seq=0)
            if after_seq > buf.seq:
                return ReplayResult(gap=True, latest_seq=latest)        # seq space was reset
            live = self._live(buf)
            if after_seq == buf.seq:
                return ReplayResult(latest_seq=latest)                  # fully caught up
            oldest = live[0].seq if live else buf.seq + 1
            if after_seq < oldest - 1:
                return ReplayResult(gap=True, latest_seq=latest)        # rolled off the ring / expired
            return ReplayResult(events=[e for e in live if e.seq > after_seq], latest_seq=latest)

    async def subscribe(self, user_id: str) -> LogSubscription:
        async with self._lock:
            sub = _MemorySubscription(self, user_id, self._queue_size)
            self._buf(user_id).subs.add(sub)
            return sub

    async def latest_seq(self, user_id: str) -> int:
        buf = self._users.get(user_id)
        return buf.seq if buf else 0

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            subs = [s for b in self._users.values() for s in list(b.subs)]
        for s in subs:
            await s.close()
        self._users.clear()

    # introspection for the resource monitor / tests
    def stats(self) -> Dict[str, int]:
        return {
            "users": len(self._users),
            "events": sum(len(b.events) for b in self._users.values()),
            "subscribers": sum(len(b.subs) for b in self._users.values()),
        }


# --------------------------------------------------------------------------- JetStream

class _JsSubscription(LogSubscription):
    def __init__(self, sub: Any) -> None:
        self._sub, self._closed = sub, False

    async def get(self, timeout: float) -> Optional[UserTaskEvent]:
        if self._closed:
            return None
        try:
            msg = await self._sub.next_msg(timeout=timeout)
        except Exception:                         # timeout / closed
            return None
        return JetStreamTaskEventLog.decode(msg)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._sub.unsubscribe()
        except Exception:
            pass


class JetStreamTaskEventLog(TaskEventLog):
    """NATS JetStream backend: durable, and shared by several backend instances.

    **Sequence numbers.** ``seq`` is the JetStream *stream* sequence of the message: strictly
    increasing for a user and stable across restarts, but NOT contiguous (other users' events
    sit between). Contiguity therefore cannot be used to detect lost events.

    **Gap detection.** Each message carries ``prev`` — the seq of that user's previous event.
    On replay from ``after``, the first returned event must have ``prev == after``; if not,
    something between was removed by the retention limits (``max_age`` / ``max_msgs_per_user``)
    and the client is told to resync. With several instances publishing for the same user at
    the same instant ``prev`` can be stale; the worst case is a spurious (harmless) resync.

    Appends for one user are serialized in-process so ``prev`` is exact for a single instance.
    """

    def __init__(self, config: JetStreamLogConfig, servers: List[str]) -> None:
        self._cfg = config
        self._servers = servers
        self._nc: Any = None
        self._js: Any = None
        self._last: "OrderedDict[str, int]" = OrderedDict()      # user -> last seq we published
        self._locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
        self._subs: set[_JsSubscription] = set()

    # ---------------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        import nats                                     # optional dependency path
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig

        self._nc = await asyncio.wait_for(
            nats.connect(self._servers, connect_timeout=self._cfg.connect_timeout_seconds,
                         max_reconnect_attempts=3, name="pai-task-events"),
            timeout=self._cfg.connect_timeout_seconds + 1.0,
        )
        self._js = self._nc.jetstream()
        wanted = StreamConfig(
            name=self._cfg.stream,
            subjects=[f"{self._cfg.subject_prefix}.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=float(self._cfg.max_age_seconds),
            max_msgs_per_subject=self._cfg.max_msgs_per_user,
        )
        try:
            await self._js.add_stream(wanted)
        except Exception:
            await self._js.update_stream(wanted)       # exists with other limits: align them
        logger.info("Task event log: JetStream stream '{}' ready ({})",
                    self._cfg.stream, ", ".join(self._servers))

    async def close(self) -> None:
        for s in list(self._subs):
            await s.close()
        if self._nc is not None:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=3.0)
            except Exception:
                try:
                    await self._nc.close()
                except Exception:
                    pass
            self._nc = self._js = None

    # ------------------------------------------------------------------------ helpers
    def _subject(self, user_id: str) -> str:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in user_id)   # no '.', '*', '>' in a token
        return f"{self._cfg.subject_prefix}.{safe}"

    @staticmethod
    def decode(msg: Any) -> Optional[UserTaskEvent]:
        try:
            body = json.loads(msg.data.decode("utf-8"))
            return UserTaskEvent(seq=msg.metadata.sequence.stream, user_id=body["user_id"],
                                 event=body["event"], data=body.get("data") or {},
                                 ts=body.get("ts") or time.time())
        except Exception as exc:
            logger.warning("task event log: undecodable message skipped: {}", exc)
            return None

    @staticmethod
    def _prev_of(msg: Any) -> Optional[int]:
        try:
            return json.loads(msg.data.decode("utf-8")).get("prev")
        except Exception:
            return None

    def _lock(self, user_id: str) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = self._locks[user_id] = asyncio.Lock()
            while len(self._locks) > 10_000:
                self._locks.popitem(last=False)
        else:
            self._locks.move_to_end(user_id)
        return lock

    # ---------------------------------------------------------------------- interface
    async def latest_seq(self, user_id: str) -> int:
        try:
            msg = await self._js.get_last_msg(self._cfg.stream, self._subject(user_id))
            return int(msg.seq)
        except Exception:                                  # NotFound: no events yet
            return 0

    async def append(self, user_id: str, event: str, data: Dict[str, Any]) -> int:
        async with self._lock(user_id):
            prev = self._last.get(user_id)
            if prev is None:
                prev = await self.latest_seq(user_id) or None
            payload = json.dumps({"user_id": user_id, "event": event, "data": data,
                                  "ts": time.time(), "prev": prev}, default=str).encode("utf-8")
            ack = await self._js.publish(self._subject(user_id), payload)
            self._last[user_id] = ack.seq
            self._last.move_to_end(user_id)
            while len(self._last) > 10_000:
                self._last.popitem(last=False)
            return int(ack.seq)

    async def replay(self, user_id: str, after_seq: int) -> ReplayResult:
        from nats.js.api import ConsumerConfig, DeliverPolicy

        latest = await self.latest_seq(user_id)
        if after_seq > latest:
            return ReplayResult(gap=True, latest_seq=latest)        # stream was recreated / wrong id
        if after_seq == latest:
            return ReplayResult(latest_seq=latest)
        sub = await self._js.subscribe(
            self._subject(user_id),
            config=ConsumerConfig(deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                                  opt_start_seq=after_seq + 1),
            ordered_consumer=True,
        )
        events: List[UserTaskEvent] = []
        first_prev_ok: Optional[bool] = None
        try:
            while True:
                try:
                    msg = await sub.next_msg(timeout=2.0)
                except Exception:
                    break
                seq = msg.metadata.sequence.stream
                if first_prev_ok is None:
                    prev = self._prev_of(msg)
                    # contiguous iff this event points back at what the client already has
                    first_prev_ok = (prev == after_seq) or (after_seq == 0 and prev is None)
                    if not first_prev_ok:
                        return ReplayResult(gap=True, latest_seq=latest)
                ev = self.decode(msg)
                if ev is not None:
                    events.append(ev)
                if seq >= latest:
                    break
        finally:
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        return ReplayResult(events=events, latest_seq=latest)

    async def subscribe(self, user_id: str) -> LogSubscription:
        from nats.js.api import ConsumerConfig, DeliverPolicy

        sub = await self._js.subscribe(
            self._subject(user_id),
            config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
        )
        wrapped = _JsSubscription(sub)
        self._subs.add(wrapped)
        return wrapped


# ----------------------------------------------------------------------------- factory

async def build_task_event_log(config: TaskEventsConfig, nats_servers: List[str]) -> TaskEventLog:
    """The configured log; falls back to memory (with a warning) if JetStream is unreachable."""
    memory = InMemoryTaskEventLog(
        buffer_size=config.buffer_size, max_users=config.max_users,
        ttl_seconds=config.ttl_seconds, subscriber_queue=config.subscriber_queue,
    )
    if config.backend.value != "jetstream":
        return memory
    js = JetStreamTaskEventLog(config.jetstream, nats_servers)
    try:
        await js.start()
        return js
    except Exception as exc:
        logger.warning("Task event log: JetStream unavailable ({}: {}) — using the in-memory log "
                       "(events will not survive a restart)", type(exc).__name__, exc)
        try:
            await js.close()
        except Exception:
            pass
        return memory
