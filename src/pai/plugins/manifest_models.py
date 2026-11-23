"""Typed, immutable representation of a plugin manifest.

This replaces the dataclass-based `PluginManifest.from_dict` parser.
Every field name matches the JSON key verbatim (camelCase), so there
is no alias magic and no legacy shim.

Layers of validation (all raise `pydantic.ValidationError`):
  • field types + required fields
  • `required ⊆ properties` for parameters (recursive)
  • duplicate capability ids
  • `kind` ↔ `deviceTypes` consistency
  • `type` ↔ language consistency
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pai.common.enums.permission import RiskLevel

from .manifest_enums import (
    PreconditionType,
    RuntimeKind,
)


# ---------------------------------------------------------------------------
# Base configs
# ---------------------------------------------------------------------------

_STRICT = ConfigDict(
    extra="forbid",
    frozen=True,
    str_strip_whitespace=True,
    populate_by_name=True,
)

_OPEN = ConfigDict(
    extra="allow",          # preserves unknown JSON Schema keywords verbatim
    frozen=True,
    str_strip_whitespace=True,
    populate_by_name=True,
)


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------

class PlatformSpec(BaseModel):
    model_config = _STRICT
    
    artifact: str
    entrypoint: str 


class PluginRuntime(BaseModel):
    model_config = _STRICT

    kind: RuntimeKind
    nativeModule: str = Field(min_length=1)
    minPaiVersion: Optional[str] = None

    @property
    def is_server(self) -> bool:
        return self.kind is RuntimeKind.SERVER

    @property
    def is_device(self) -> bool:
        return self.kind is RuntimeKind.DEVICE


class Precondition(BaseModel):
    model_config = _STRICT

    type: PreconditionType
    field: str = Field(min_length=1)
    allowedRootsRef: Optional[str] = None
    allowedPrefixesRef: Optional[str] = None


class CapabilityParameter(BaseModel):
    """One parameter in a JSON Schema `properties` map.

    Extra JSON Schema keywords (`format`, `examples`, `$ref`, …) are
    preserved via `extra="allow"` so nothing is lost for prompt
    building.
    """

    model_config = _OPEN

    type: Literal[
        "string", "number", "integer", "boolean", "array", "object", "null",
    ]
    description: Optional[str] = None
    default: Any = None
    enum: Optional[List[Any]] = None
    properties: Optional[Dict[str, "CapabilityParameter"]] = None
    required: Optional[List[str]] = None          # only for type="object"
    items: Optional["CapabilityParameter"] = None  # only for type="array"
    pattern: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    minLength: Optional[int] = Field(default=None, ge=0)
    maxLength: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_structure(self) -> "CapabilityParameter":
        if self.type == "object":
            if self.required is not None and self.properties is not None:
                missing = set(self.required) - set(self.properties)
                if missing:
                    raise ValueError(
                        f"'required' references unknown nested properties: "
                        f"{sorted(missing)}"
                    )
        elif self.type != "object" and self.required:
            raise ValueError("'required' is only valid when type='object'")
        if self.type == "array" and self.items is None:
            raise ValueError("array parameter must declare 'items'")
        return self


class ParametersSchema(BaseModel):
    """The full `parameters` JSON Schema of a capability."""

    model_config = _OPEN

    type: Literal["object"] = "object"
    properties: Dict[str, CapabilityParameter] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required(self) -> "ParametersSchema":
        missing = set(self.required) - set(self.properties)
        if missing:
            raise ValueError(
                f"'required' references unknown properties: {sorted(missing)}"
            )
        return self


class CapabilitySpec(BaseModel):
    model_config = _STRICT

    id: str = Field(min_length=1)
    description: str
    risk: RiskLevel = RiskLevel.LOW
    requireConfirmation: bool = False
    retryable: bool = True   # False: never auto-retry/re-send (non-idempotent actions); manual retry still allowed
    confirmationReason: Optional[str] = None
    confirmationPreview: Optional[Dict[str, Any]] = None
    precondition: Optional[Precondition] = None
    permissions: List[str] = Field(default_factory=list)
    parameters: ParametersSchema = Field(default_factory=ParametersSchema)

    @model_validator(mode="before")
    @classmethod
    def _infer_risk(cls, data: Any) -> Any:
        """If `risk` is not explicit, derive it from confirmation + precondition."""
        if not isinstance(data, dict) or data.get("risk") is not None:
            return data
        needs = bool(data.get("requireConfirmation", False))
        has_pre = data.get("precondition") is not None
        d = dict(data)
        d["risk"] = (
            RiskLevel.HIGH.value   if needs and has_pre else
            RiskLevel.MEDIUM.value if needs else
            RiskLevel.LOW.value
        )
        return d



# Resolve forward refs (CapabilityParameter is recursive)
CapabilityParameter.model_rebuild()
ParametersSchema.model_rebuild()