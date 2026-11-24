from enum import Enum, StrEnum

from pydantic import BaseModel, Field


class TaskEventsBackend(StrEnum):
    MEMORY = "memory"          # in-process ring buffer (default; single backend instance)
    JETSTREAM = "jetstream"    # NATS JetStream: durable, shared by several backend instances


class JetStreamLogConfig(BaseModel):
    """Used only when ``backend: jetstream`` (connection comes from ``event_bus.nats``)."""
    stream: str = Field(default="PAI_TASK_EVENTS")
    subject_prefix: str = Field(default="pai.tasks")           # events go to <prefix>.<user_id>
    max_age_seconds: int = Field(default=24 * 3600, ge=60)     # how long a missed event can be replayed
    max_msgs_per_user: int = Field(default=1000, ge=10)
    connect_timeout_seconds: float = Field(default=3.0, gt=0)


class TaskEventsConfig(BaseModel):
    """Per-user task event stream: ``GET /tasks/events`` (SSE) with replay after a reconnect."""
    backend: TaskEventsBackend = Field(default=TaskEventsBackend.MEMORY)
    buffer_size: int = Field(default=1000, ge=10)              # events kept per user (memory backend)
    max_users: int = Field(default=10_000, ge=10)              # users tracked at once (LRU)
    ttl_seconds: int | None = Field(default=None, ge=1)        # optional max age of a buffered event
    subscriber_queue: int = Field(default=256, ge=8)           # per-connection queue (slow client => resync)
    jetstream: JetStreamLogConfig = Field(default_factory=JetStreamLogConfig)
