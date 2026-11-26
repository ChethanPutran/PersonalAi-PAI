from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from pai.security.consent.models import ConsentRecord
from .request import AuthorizationRequest


class ScopeContext(BaseModel):
    """TRUSTED server-side scope configuration consumed by the scope evaluators.

    Typed on purpose: a misspelt key is a validation error, not a silent
    "no roots configured". Never derived from LLM output or call parameters.
    """
    model_config = ConfigDict(frozen=True)

    allowed_devices: list[str] = []
    filesystem_roots: list[str] = []
    filesystem_denied: list[str] = []         # always refused, even inside an allowed root
    filesystem_path_optional: bool = False   # the capability's manifest does not require a path
    network_hosts: list[str] = []
    user_roles: list[str] = []

    @classmethod
    def from_execution_context(cls, ctx: Any) -> "ScopeContext":
        """Pick only the known trusted keys from ExecutionContext.metadata."""
        meta = getattr(ctx, "metadata", None) or {}
        return cls(**{k: list(meta[k]) for k in cls.model_fields if k in meta})

    def with_device(self, device_id: Optional[str]) -> "ScopeContext":
        """This scope plus the device the SERVER selected for the run.

        The DeviceSelector only chooses among the user's own connected devices, so the chosen
        target is trusted; without it every device-scoped permission (camera, device control)
        is denied with "Device is not allowlisted" on the chat/plan/voice paths.
        """
        if not device_id or device_id in self.allowed_devices:
            return self
        return self.model_copy(update={"allowed_devices": [*self.allowed_devices, device_id]})


class ManifestFacts(BaseModel):
    """Trusted facts taken from the plugin manifest / the approving user.

    Never populated from LLM output or call parameters.
    """
    model_config = ConfigDict(frozen=True)

    requires_confirmation: bool = False        # CapabilitySpec.requireConfirmation
    confirmation_reason: Optional[str] = None  # CapabilitySpec.confirmationReason
    approved: bool = False                     # the user explicitly approved this request


@dataclass
class AuthorizationContext:
    """Mutable scratchpad for one authorization run.

    The caller supplies only ``request`` and ``manifest``; the AuthorizationManager
    fills in the rest while evaluating.
    """
    request: AuthorizationRequest
    manifest: ManifestFacts = field(default_factory=ManifestFacts)
    permission: Any = None
    consent: ConsentRecord | None = None
    scope_result: Any = None
    risk: Any = None  # RiskAssessment, set by the pipeline
    policy_results: list[Any] = field(default_factory=list)
