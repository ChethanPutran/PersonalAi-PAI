from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid import uuid4
from pai.storage.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================
# Chat
# ==========================================================
from sqlalchemy import Text

class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(
        String(256), default="New chat", nullable=False,
    )
    message_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageModel.created_at",
    )

    __table_args__ = (
        Index("ix_chat_sessions_user_last", "user_id", "last_message_at"),
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False,   # 'user' | 'assistant' | 'system'
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Populated only on assistant messages that ran a plan.
    plan_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True,
    )

    session: Mapped["ChatSessionModel"] = relationship(
        back_populates="messages",
    )
    
# ============================================================
# Users
# ============================================================

class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    email: Mapped[str] = mapped_column(
        String(256), unique=True, nullable=False, index=True,
    )
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)

    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ---- relationships ----
    plugins: Mapped[list["UserPluginModel"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    devices: Mapped[list["DeviceModel"]] = relationship(
        back_populates="user",
    )


# ============================================================
# Devices
# ============================================================

class DeviceModel(Base):
    """
    Persistent device record.

    Mirrors what the in-memory DeviceRegistry holds, so devices survive
    backend restarts. This is what makes 'Device not found' go away after
    a reboot.
    """

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    platform_version: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    os_version: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    architecture: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    hostname: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    app_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    runtime_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    status: Mapped[str] = mapped_column(String(32), default="offline", nullable=False)
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    user_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        index=True,
    )

    capabilities: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ---- relationships ----
    user: Mapped[Optional["UserModel"]] = relationship(back_populates="devices")
    plugins: Mapped[list["DevicePluginModel"]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
    )


# ============================================================
# User plugins
# ============================================================

class UserPluginModel(Base):
    """
    Per-user plugin AUTHORIZATION and CONFIG.

    is_enabled means: "user X has authorized this plugin".
    It does NOT mean the plugin is installed on any particular device.

    metadata holds the user's plugin configuration (JSON).
    """

    __tablename__ = "user_plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ---- relationship ----
    user: Mapped["UserModel"] = relationship(back_populates="plugins")

    __table_args__ = (
        UniqueConstraint("user_id", "plugin_id", name="uq_user_plugin"),
    )


# ============================================================
# Schedules
# ============================================================

class ScheduleModel(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    target_device_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True,
    )

    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    kind: Mapped[str] = mapped_column(String(16))
    fire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cron: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    interval_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    capability: Mapped[str] = mapped_column(
        String(128), default="system.notifications.show",
    )
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    catch_up: Mapped[str] = mapped_column(String(8), default="once")

    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True,
    )
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_fire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("ix_schedules_due", "enabled", "next_fire_at"),)

# ============================================================
# Tasks
# ============================================================
class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)

    # --- indexed query/sort columns (denormalized from payload) ---
    status: Mapped[str] = mapped_column(String(16), index=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        String, index=True, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True,
    )

    # --- full serialized Task — source of truth ---
    payload: Mapped[dict] = mapped_column(JSON)

    # --- housekeeping ---
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_status_created", "status", "created_at"),
    )


# ============================================================
# Device plugins
# ============================================================

class DevicePluginModel(Base):
    """
    Per-device plugin INSTALLATION state.

    One row per (device_id, plugin_id). Updated when:
      - the backend sends plugin.install and the device confirms success
      - the device uninstalls a plugin
      - the device reports its installed set on connect (reconciliation)

    'enabled_on_device' means the .so is currently loaded by the host
    process on that device. It's independent of the user's is_enabled
    flag in user_plugins.
    """

    __tablename__ = "device_plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    device_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)

    is_installed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled_on_device: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    installed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False,
    )

    # ---- relationship ----
    device: Mapped["DeviceModel"] = relationship(back_populates="plugins")

    __table_args__ = (
        UniqueConstraint("device_id", "plugin_id", name="uq_device_plugin"),
    )