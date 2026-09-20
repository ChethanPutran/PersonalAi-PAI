from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pai.storage.database import Base


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UserModel(Base):
    """
    Persistent user record.

    This is intentionally small. Authentication-specific information
    can be added later when the authentication subsystem is finalized.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    plugins: Mapped[list["UserPluginModel"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserPluginModel(Base):
    """
    Persistent per-user plugin configuration.

    IMPORTANT:
    is_enabled represents USER AUTHORIZATION/PREFERENCE.

    It does NOT represent whether the plugin runtime is currently
    initialized or running.
    """

    __tablename__ = "user_plugins"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    plugin_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    user: Mapped["UserModel"] = relationship(
        back_populates="plugins",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "plugin_id",
            name="uq_user_plugin",
        ),
    )