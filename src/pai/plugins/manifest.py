# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field

from pai.common.enums.device import DeviceArchitecture, DevicePlatform, DeviceType
from pai.common.enums.permission import PermissionResource
from pai.plugins.manifest_enums import *
from pai.plugins.manifest_models import *
from pai.plugins.manifest_models import _STRICT


class PluginManifest(BaseModel):
    model_config = _STRICT

    # ── identity ────────────────────────────────────────────────
    schemaVersion: int = Field(ge=1)
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9._-]*$")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    author: str = Field(min_length=1)
    license: str = Field(min_length=1)

    # ── runtime (where + how it's loaded) ───────────────────────
    type: PluginLanguage
    runtime: PluginRuntime

    # ── targeting ───────────────────────────────────────────────
    platforms: Dict[DevicePlatform, PlatformSpec] = Field(default_factory=dict)
    architectures: List[DeviceArchitecture] = Field(default_factory=list)
    deviceTypes: List[DeviceType] = Field(default_factory=list)

    # ── policy ──────────────────────────────────────────────────
    permissions: List[PermissionResource] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    postInstall: List[List[str]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # ── capabilities ────────────────────────────────────────────
    capabilities: List[CapabilitySpec] = Field(default_factory=list)
    configSchema: Dict[str, Any] = Field(default_factory=dict)

    # ── validators ──────────────────────────────────────────────
    @model_validator(mode="after")
    def _finalize(self) -> "PluginManifest":
        # kind ↔ deviceTypes
        if self.runtime.kind is RuntimeKind.SERVER:
            if "server" not in self.deviceTypes:
                raise ValueError(
                    "runtime.kind='server' requires 'server' in deviceTypes"
                )
        else:
            if "server" in self.deviceTypes:
                raise ValueError(
                    "runtime.kind='device' must not include 'server' "
                    "in deviceTypes"
                )
        if not self.capabilities:
            raise ValueError(
                f"plugin {self.id!r} must declare at least one capability"
            )
    
        # duplicate capability ids
        ids = [c.id for c in self.capabilities]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate capability ids: {dupes}")

        return self

    # ── convenience API (kept from the dataclass version) ───────
    @property
    def capability_names(self) -> List[str]:
        return [c.id for c in self.capabilities]

    @property
    def capability_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Raw JSON Schema per capability — for LLM prompt building.

        Uses `model_dump()` which includes Pydantic `extra="allow"`
        fields, so every original JSON-Schema keyword is preserved.
        """
        return {
            c.id: {
                "description": c.description,
                "parameters": c.parameters.model_dump(exclude_none=True),
            }
            for c in self.capabilities
        }

    def get_capability(self, capability_id: str) -> Optional[CapabilitySpec]:
        for c in self.capabilities:
            if c.id == capability_id:
                return c
        return None

    def validate_capability(self, capability_id: str) -> None:
        if capability_id not in self.capability_names:
            raise ValueError(
                f"Plugin {self.id!r} has no capability {capability_id!r} "
                f"(has: {self.capability_names})"
            )

    # ── legacy-shaped entry point kept for compatibility ────────
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        return cls.model_validate(data)

    # ── catalog projection ──────────────────────────────────────
    def to_info(
        self,
        *,
        packageUrl: Optional[str] = None,
        checksum: Optional[str] = None,
        size: int = 0,
    ) -> "PluginInfo":
        """Project this manifest into the lightweight catalog view.

        Uses a deferred import so `manifest_models` does not depend on
        `manifest_info` at module-load time (which would create a
        circular import — `manifest_info` imports `manifest_models`).

        `packageUrl` / `checksum` / `size` are *not* manifest fields;
        they are supplied by the packager or registry that produced the
        tarball. Pass them here, or call `.with_packaging(...)` on the
        result.
        """
        from .manifest_info import PluginInfo 
        return PluginInfo.from_manifest(
            self,
            packageUrl=packageUrl,
            checksum=checksum,
            size=size,
        )
