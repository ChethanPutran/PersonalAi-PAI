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
    network_hosts: list[str] = []
    user_roles: list[str] = []

    @classmethod
    def from_execution_context(cls, ctx: Any) -> "ScopeContext":
        """Pick only the known trusted keys from ExecutionContext.metadata."""
        meta = getattr(ctx, "metadata", None) or {}
        return cls(**{k: list(meta[k]) for k in cls.model_fields if k in meta})


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
