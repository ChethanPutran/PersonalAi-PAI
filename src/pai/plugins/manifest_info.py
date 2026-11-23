"""Compressed, display-only view of a PluginManifest.

This is what the catalog holds (`PluginManager._catalog`) and what
`GET /api/v1/plugins` serialises. It carries no runtime state, no
JSON Schemas, no raw metadata — just enough for a client to render a
plugin list.

If a client needs the full picture (parameter schemas, preconditions,
artifacts per platform), it fetches the full manifest via
`GET /api/v1/plugins/{id}`.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from pai.common.enums.device import DeviceArchitecture, DevicePlatform, DeviceType
from pai.common.enums.permission import PermissionResource
from pai.plugins.manifest_models import PlatformSpec, PluginRuntime

from .manifest_enums import PluginLanguage, RuntimeKind


class PluginInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # ── identity ────────────────────────────────────────────────
    id: str
    name: str
    version: str
    description: str = ""

    # ── classification ──────────────────────────────────────────
    type: PluginLanguage            # native | kotlin | python | swift | javascript
    runtimeKind: RuntimeKind        # server | device

    # ── targeting ───────────────────────────────────────────────
    platforms: Dict[DevicePlatform, PlatformSpec] = Field(default_factory=dict)
    architectures: List[DeviceArchitecture] = Field(default_factory=list)
    deviceTypes: List[DeviceType] = Field(default_factory=list)
    permissions: List[PermissionResource] = Field(default_factory=list)
    runtimeSpec: PluginRuntime 

    # ── capabilities ────────────────────────────────────────────
    capabilities: List[str] = Field(default_factory=list)
    requiresConfirmation: bool = False       # true if *any* capability does

    # ── catalog / packaging metadata ────────────────────────────
    packageUrl: Optional[str] = None
    checksum: Optional[str] = None
    size: int = 0

    # ── convenience ─────────────────────────────────────────────
    @property
    def isServer(self) -> bool:
        return self.runtimeKind is RuntimeKind.SERVER

    @property
    def isDevice(self) -> bool:
        return self.runtimeKind is RuntimeKind.DEVICE

    # ── construction ────────────────────────────────────────────
    @classmethod
    def from_manifest(
        cls,
        m: "PluginManifest",
        *,
        packageUrl: Optional[str] = None,
        checksum: Optional[str] = None,
        size: int = 0,
    ) -> "PluginInfo":
        """Build a catalog entry from a fully-validated manifest.

        `packageUrl` / `checksum` / `size` are *not* manifest fields —
        they are filled in by the packager / registry that produced the
        tarball. Pass them here (or via `with_packaging()` later).
        """
        return cls(
            id=m.id,
            name=m.name,
            version=m.version,
            description=m.description,
            type=m.type,
            runtimeKind=m.runtime.kind,
            runtimeSpec=m.runtime,
            platforms=m.platforms,
            architectures=list(m.architectures),
            deviceTypes=list(m.deviceTypes),
            permissions=list(m.permissions),
            capabilities=[c.id for c in m.capabilities],
            requiresConfirmation=any(c.requireConfirmation for c in m.capabilities),
            packageUrl=packageUrl,
            checksum=checksum,
            size=size,
        )

    def with_packaging(
        self,
        *,
        packageUrl: Optional[str] = None,
        checksum: Optional[str] = None,
        size: Optional[int] = None,
    ) -> "PluginInfo":
        """Return a copy with packaging metadata attached (immutable update)."""
        update: dict = {}
        if packageUrl is not None:
            update["packageUrl"] = packageUrl
        if checksum is not None:
            update["checksum"] = checksum
        if size is not None:
            update["size"] = size
        return self.model_copy(update=update)