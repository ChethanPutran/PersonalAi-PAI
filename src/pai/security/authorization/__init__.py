"""Authorization subsystem.

- ``request`` / ``decision``: the shared request & decision types.
- ``manager.AuthorizationManager``: the policy pipeline (permission -> scope ->
  risk -> consent -> policies -> token).
- ``service.AuthorizationService``: orchestration-level gate (plugin resolution,
  per-user enablement, security check, device check). Import it from
  ``pai.security.authorization.service`` (kept out of here to avoid heavy
  imports / cycles).
"""
from .decision import AuthorizationDecision, DecisionEffect
from .enforcement import AuthorizationDenied, enforce
from .request import AuthorizationRequest

__all__ = ["AuthorizationDecision", "AuthorizationDenied", "AuthorizationRequest", "DecisionEffect", "enforce"]
