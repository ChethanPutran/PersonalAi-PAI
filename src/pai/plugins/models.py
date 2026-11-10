from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Catalog-level types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PluginInfo:
    """
    Lightweight catalog entry for a plugin.

    This is what `PluginManager._catalog` holds and what the HTTP
    `/api/v1/plugins` endpoint serialises. It is populated from a
    `PluginManifest` (via `PluginManager._manifest_to_info`) or from a
    static entry in `PLUGIN_CATALOG`.
    """
    id: str
    name: str
    version: str

    platforms: List[str] = field(default_factory=list)
    architectures: List[str] = field(default_factory=list)
    device_types: List[str] = field(default_factory=list)

    capabilities: List[str] = field(default_factory=list)

    # capability_id -> {"description": str, "parameters": <JSON Schema>}
    capability_schemas: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    description: str = ""
    plugin_type: str = "action"

    package_url: Optional[str] = None
    checksum: Optional[str] = None
    size: int = 0


# ---------------------------------------------------------------------------
# Manifest-level types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityParameter:
    """
    Structured form of one parameter in a capability's JSON Schema.

    This is the parsed view used when a caller needs to iterate
    parameters programmatically (e.g. building a config form).
    The raw JSON Schema is also preserved on
    `PluginManifest.capability_schemas[cap_id]["parameters"]` so
    nothing is lost for prompt-building.
    """
    type: str
    required: bool = False
    description: Optional[str] = None
    default: Any = None
    enum: Optional[List[Any]] = None


@dataclass(frozen=True)
class CapabilitySpec:
    """Declarative description of one plugin capability."""
    name: str
    description: str = ""
    risk: str = "low"
    parameters: Dict[str, CapabilityParameter] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginRuntime:
    """Runtime information required to load a plugin."""
    kind: str                    # "server" | "device" | "dart"
    entry_point: str = ""
    class_name: str = ""
    native_module: str = ""

    @property
    def is_server(self) -> bool:
        return self.kind == "server"

    @property
    def is_device(self) -> bool:
        return self.kind == "device"

    @property
    def is_dart(self) -> bool:
        return self.kind == "dart"


@dataclass(frozen=True)
class PluginManifest:
    """
    Immutable representation of manifest.json.

    Metadata only — it carries no runtime state. It is produced by
    `PluginRegistry.discover()` (server plugins) or by
    `PluginManager._load_device_plugin()` (native plugins in
    `plugin_registry/`) and consumed by `PluginManager._manifest_to_info`
    which builds the catalog `PluginInfo`.
    """

    id: str
    name: str
    version: str
    plugin_type: str
    description: str
    runtime: PluginRuntime

    platforms: List[str] = field(default_factory=list)
    architectures: List[str] = field(default_factory=list)
    device_types: List[str] = field(default_factory=list)

    permissions: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    capabilities: List[CapabilitySpec] = field(default_factory=list)

    # capability_id -> {"description": str, "parameters": <raw JSON Schema>}
    capability_schemas: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    config_schema: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def capability_names(self) -> List[str]:
        return [c.name for c in self.capabilities]

    def get_capability(self, capability_name: str) -> Optional[CapabilitySpec]:
        for c in self.capabilities:
            if c.name == capability_name:
                return c
        return None

    def validate_capability(self, capability_name: str) -> None:
        if capability_name not in self.capability_names:
            raise ValueError(
                f"Plugin '{self.id}' does not provide capability "
                f"'{capability_name}'"
            )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        plugin_id = data.get("id")
        if not plugin_id:
            raise ValueError("Manifest has no 'id'")

        runtime_data = data.get("runtime") or {}
        if not runtime_data:
            raise ValueError(
                f"Plugin '{plugin_id}' is missing runtime configuration"
            )

        kind_raw = (runtime_data.get("kind") or "").lower()
        runtime = cls._parse_runtime(plugin_id, kind_raw, runtime_data)

        capabilities, capability_schemas = cls._parse_capabilities(
            data.get("capabilities") or []
        )

        platforms = cls._parse_str_list_field(data.get("platforms"))
        architectures = cls._parse_str_list_field(data.get("architectures"))
        device_types = cls._parse_str_list_field(data.get("device_types"))

        return cls(
            id=str(plugin_id),
            name=str(data.get("name") or plugin_id),
            version=str(data.get("version", "1.0.0")),
            plugin_type=str(
                data.get("type") or data.get("plugin_type") or "action"
            ),
            description=str(data.get("description") or ""),
            runtime=runtime,
            platforms=platforms,
            architectures=architectures,
            device_types=device_types,
            permissions=list(data.get("permissions") or []),
            dependencies=list(data.get("dependencies") or []),
            capabilities=capabilities,
            capability_schemas=capability_schemas,
            config_schema=data.get("config_schema") or {},
        )

    # ------------------------------------------------------------------
    # Parsing helpers — one per manifest section
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_runtime(
        plugin_id: str,
        kind_raw: str,
        runtime_data: Dict[str, Any],
    ) -> PluginRuntime:
        # Device plugins — "native" is what the camera/notifications
        # manifests use; "device" is the newer alias.
        if kind_raw in ("native", "device"):
            native_module = (
                runtime_data.get("nativeModule")
                or runtime_data.get("native_module")
                or runtime_data.get("module")
            )
            if not native_module:
                raise ValueError(
                    f"Plugin '{plugin_id}' missing runtime.nativeModule"
                )
            return PluginRuntime(
                kind="device", native_module=str(native_module),
            )

        # Server-side Python plugins
        if kind_raw in ("server", "python"):
            entry_point = (
                runtime_data.get("entry_point")
                or runtime_data.get("entry")
            )
            class_name = (
                runtime_data.get("class")
                or runtime_data.get("class_name")
            )
            if not entry_point:
                raise ValueError(
                    f"Plugin '{plugin_id}' missing runtime.entry_point"
                )
            if not class_name:
                raise ValueError(
                    f"Plugin '{plugin_id}' missing runtime.class"
                )
            return PluginRuntime(
                kind="server",
                entry_point=str(entry_point),
                class_name=str(class_name),
            )

        # Dart plugins — not currently discovered but part of the schema
        if kind_raw == "dart":
            return PluginRuntime(
                kind="dart",
                entry_point=str(runtime_data.get("entry_point", "")),
            )

        raise ValueError(
            f"Plugin '{plugin_id}' has unknown runtime.kind {kind_raw!r}"
        )

    @classmethod
    def _parse_capabilities(
        cls, entries: Any,
    ) -> tuple[List[CapabilitySpec], Dict[str, Dict[str, Any]]]:
        """
        Parse the manifest's `capabilities` array.

        Returns (parsed_specs, raw_schema_map). Both are populated from
        the same source, so nothing is lost — the specs give typed
        parameter objects, and the raw schemas preserve enums, defaults,
        and any other JSON Schema keyword the goal decomposer needs.
        """
        capabilities: List[CapabilitySpec] = []
        schemas: Dict[str, Dict[str, Any]] = {}

        if not isinstance(entries, list):
            return capabilities, schemas

        for entry in entries:
            # Old format: bare string id
            if isinstance(entry, str):
                cap_id = entry
                description = ""
                cap_parameters: Dict[str, Any] = {}
                risk = "low"

            # New format: {id, description, risk?, parameters?}
            elif isinstance(entry, dict):
                cap_id = entry.get("id") or entry.get("name")
                if not cap_id:
                    continue
                description = entry.get("description") or ""
                cap_parameters = entry.get("parameters") or {}
                risk = entry.get("risk", "low")

            else:
                continue

            # Extract JSON Schema properties + required list
            if isinstance(cap_parameters, dict):
                properties = cap_parameters.get("properties") or {}
                required_raw = cap_parameters.get("required") or []
            else:
                properties = {}
                required_raw = []

            required = (
                set(required_raw)
                if isinstance(required_raw, list)
                else set()
            )

            parameters: Dict[str, CapabilityParameter] = {}
            if isinstance(properties, dict):
                for pname, pspec in properties.items():
                    if not isinstance(pspec, dict):
                        continue

                    enum_raw = pspec.get("enum")
                    parameters[pname] = CapabilityParameter(
                        type=str(pspec.get("type", "any")),
                        required=pname in required,
                        description=pspec.get("description"),
                        default=pspec.get("default"),
                        enum=(
                            list(enum_raw)
                            if isinstance(enum_raw, list)
                            else None
                        ),
                    )

            capabilities.append(
                CapabilitySpec(
                    name=str(cap_id),
                    description=description,
                    risk=risk,
                    parameters=parameters,
                )
            )

            # Keep the raw schema verbatim — `CapabilityParameter` only
            # captures type/required/description/default/enum, but the
            # decomposer builds prompts from the raw JSON Schema so that
            # any future keyword (pattern, minLength, etc.) is preserved.
            schemas[cap_id] = {
                "description": description,
                "parameters": cap_parameters,
            }

        return capabilities, schemas

    @staticmethod
    def _parse_str_list_field(value: Any) -> List[str]:
        """
        Accept either a dict (use its keys) or a list (stringify each).
        Handles the common `"platforms": {"linux": {...}, "android": {...}}`
        shape as well as `"platforms": ["linux", "android"]`.
        """
        if isinstance(value, dict):
            return [str(k) for k in value.keys()]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []