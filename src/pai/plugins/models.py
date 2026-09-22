from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional



    
@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    version: str

    platforms: List[str]
    architectures: List[str]

    capabilities: List[str]

    package_url: Optional[str] = None
    checksum: Optional[str] = None

    size: int = 0

@dataclass(frozen=True)
class CapabilityParameter:
    """Definition of a capability parameter."""

    type: str
    required: bool = False
    description: Optional[str] = None
    default: Any = None


@dataclass(frozen=True)
class CapabilitySpec:
    """Declarative description of one plugin capability."""

    name: str
    description: str = ""
    risk: str = "low"
    parameters: Dict[str, CapabilityParameter] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class PluginRuntime:
    """Runtime information required to load a plugin."""
    kind: str                      # "server" | "device" | "dart"
    entry_point: str = ""
    class_name: str = ""
    native_module: str = ""

    # ---- server ----
    @property
    def is_server(self) -> bool:
        return self.kind == "server"

    # ---- device ----
    @property
    def is_device(self) -> bool:
        return self.kind == "device"


@dataclass(frozen=True)
class PluginManifest:
    """
    Immutable representation of manifest.json.

    This is metadata only. It does not contain runtime plugin state.
    """

    id: str
    name: str
    version: str
    plugin_type: str
    description: str
    runtime: PluginRuntime
    platforms: List[str] = field(default_factory=list)

    permissions: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    capabilities: List[CapabilitySpec] = field(
        default_factory=list
    )

    config_schema: Dict[str, Any] = field(
        default_factory=dict
    )

    @property
    def capability_names(self) -> list[str]:
        return [capability.name for capability in self.capabilities]

    def get_capability(
        self,
        capability_name: str,
    ) -> Optional[CapabilitySpec]:
        for capability in self.capabilities:
            if capability.name == capability_name:
                return capability

        return None

    def validate_capability(self, capability_name: str) -> None:
        if capability_name not in self.capability_names:
            raise ValueError(
                f"Plugin '{self.id}' does not provide "
                f"capability '{capability_name}'"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        runtime_data = data.get("runtime", {}) or {}
        kind = runtime_data.get("kind", "server")

        if kind == "server":
            entry_point = runtime_data.get("entry_point")
            class_name  = runtime_data.get("class")
            if not entry_point:
                raise ValueError(f"Plugin '{data.get('id')}' missing runtime.entry_point")
            if not class_name:
                raise ValueError(f"Plugin '{data.get('id')}' missing runtime.class")
            runtime = PluginRuntime(kind="server", entry_point=entry_point, class_name=class_name)

        elif kind == "device":
            native_module = runtime_data.get("nativeModule")
            if not native_module:
                raise ValueError(f"Plugin '{data.get('id')}' missing runtime.nativeModule")
            runtime = PluginRuntime(kind="device", native_module=native_module)

        else:
            raise ValueError(f"Unknown runtime.kind: {kind}")

        if not runtime_data:
            raise ValueError(
                f"Plugin '{data.get('id', '<unknown>')}' "
                "is missing runtime configuration"
            )

        entry_point = runtime_data.get("entry_point")
        class_name = runtime_data.get("class")

        if not entry_point:
            raise ValueError("Plugin manifest is missing runtime.entry_point")

        if not class_name:
            raise ValueError("Plugin manifest is missing runtime.class")

        capabilities: list[CapabilitySpec] = []

        for capability in data.get("capabilities", []):
            parameters: Dict[str, CapabilityParameter] = {}

            for name, parameter in capability.get(
                "parameters", {}
            ).items():
                parameters[name] = CapabilityParameter(
                    type=parameter.get("type", "any"),
                    required=parameter.get("required", False),
                    description=parameter.get("description"),
                    default=parameter.get("default"),
                )

            capabilities.append(
                CapabilitySpec(
                    name=capability["name"],
                    description=capability.get("description", ""),
                    risk=capability.get("risk", "low"),
                    parameters=parameters,
                )
            )

        return cls(
            id=data["id"],
            name=data["name"],
            version=data.get("version", "1.0.0"),
            plugin_type=data.get(
                "type",
                data.get("plugin_type", "action"),
            ),
            description=data.get("description", ""),
            runtime=PluginRuntime(
                entry_point=entry_point,
                class_name=class_name,
            ),
            permissions=data.get("permissions", []),
            dependencies=data.get("dependencies", []),
            capabilities=capabilities,
            config_schema=data.get("config_schema", {}),
        )
