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

    # Profile (settings page / apps). All optional.
    display_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)    # IANA, e.g. "Asia/Kolkata"
    locale: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)      # e.g. "en", "hi-IN"

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

    # What the OWNER lets their OTHER devices (and scheduled/agent runs) do to this one.
    # Off by default: a device only accepts remote plugin installs / invocations once its user
    # turns the matching switch on. Enforced server-side and again on the device.
    allow_remote_install: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False)
    allow_remote_invoke: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False)

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


class TrustedDeviceModel(Base):
    __tablename__ = "trusted_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    device_id: Mapped[str] = mapped_column(String, index=True)
    trusted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # A user trusts each device at most once
        {"sqlite_autoincrement": True},
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


class PluginAuditModel(Base):
    __tablename__ = "plugin_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    device_id: Mapped[str] = mapped_column(String, index=True)
    source_device_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    capability: Mapped[str] = mapped_column(String(128), index=True)
    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_status: Mapped[str] = mapped_column(String(32))  # allowed|denied|confirmed|cancelled|completed|failed
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True,
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
    # How late (seconds) a missed fire may still run. NULL = no limit. See SchedulerService._fire.
    misfire_grace_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

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

    # ---- scheduled plugin calls: validation result, standing consent, last outcome ----
    # (Added after the first release: see pai.storage.migrations for the SQLite upgrade.)
    requires_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
    )
    risk: Mapped[str] = mapped_column(String(16), default="low", server_default="low")
    executor: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    consent_granted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    consent_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    consent_granted_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # sha256 of (user, capability, canonical parameters, device): the consent is only valid
    # while the schedule still matches exactly what the user approved.
    consent_binding_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_run_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    last_run_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

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
# Prompt renders
# ============================================================
class PromptRenderModel(Base):
    __tablename__ = "prompt_renders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_id: Mapped[str] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str] = mapped_column(String(32), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rendered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True,
    )
    rendered_length: Mapped[int] = mapped_column(Integer)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    
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

    # What the user has allowed ON THE DEVICE for this plugin, as the device last reported it:
    # {"screen": "granted" | "denied" | "pending"}. ADVISORY: the device re-checks before every invoke, so
    # this only lets the server say "not allowed on that device" early and stop offering the capability.
    # None / a missing key = unknown (never reported), which blocks nothing.
    permissions: Mapped[Optional[dict[str, str]]] = mapped_column(JSON, nullable=True)
    permissions_reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

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


# ============================================================
# Consent grants ("allow for this session" / "always allow")
# ============================================================
class ConsentGrantModel(Base):
    """A standing approval. See pai.security.consent.models.ConsentGrant for the semantics."""
    __tablename__ = "consent_grants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    plugin_id: Mapped[str] = mapped_column(String(128))
    capability: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(16))
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    binding_mode: Mapped[str] = mapped_column(String(8), default="exact")
    binding_keys: Mapped[list] = mapped_column(JSON, default=list)
    params_hash: Mapped[str] = mapped_column(String(64), default="")
    params_preview: Mapped[list] = mapped_column(JSON, default=list)
    risk: Mapped[str] = mapped_column(String(16), default="low")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    granted_by: Mapped[str] = mapped_column(String)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_consent_lookup", "user_id", "plugin_id", "capability", "device_id"),
    )


class PluginOperationModel(Base):
    """One remote plugin install / uninstall, from request to outcome.

    The row is the single source of truth for "what is happening to plugin P on device D":
    the device reports progress frames, the server moves ``status`` forward (never back) and
    pushes every change to the user's connected devices, so no client has to poll.
    """

    __tablename__ = "plugin_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)          # install | uninstall
    # requested | delivered | downloading | verifying | installing | awaiting_permission
    #   | succeeded | failed
    status: Mapped[str] = mapped_column(String(24), default="requested", nullable=False, index=True)
    requested_by_device: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    error_code: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # plain words, for the user
    error_hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # what to do about it
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # raw text, for logs/support

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_plugin_op_target", "device_id", "plugin_id"),)
