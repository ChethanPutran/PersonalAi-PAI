from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pai.storage.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        nullable=True,
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