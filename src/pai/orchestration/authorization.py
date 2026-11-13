"""Authorization boundary for orchestration.

No executor should be reached before this layer succeeds.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from loguru import logger

from pai.event_bus.bus import EventBus
from pai.observability.tracer import get_tracer
from pai.common.manager import Manager

class AuthorizationError(PermissionError):
    """Raised when an orchestration action is not authorized."""


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    capability: str
    plugin: Optional[str] = None
    device_id: Optional[str] = None
    reason: Optional[str] = None


# ============================================================================
# Signature introspection
# ============================================================================

def _call_kwargs_for(
    fn: Callable[..., Any],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Return the subset of `kwargs` that `fn` actually accepts.

    Handles three signature styles:
      - Explicit named parameters:   def authorize(*, user_id, capability, ...)
      - `**kwargs` catch-all:        def authorize(**kw)
      - Positional-only             falls back to whatever overlaps

    Returns only keys present in the signature, so the call site is
    immune to drift between AuthorizationManager and the concrete
    SecurityManager/DeviceManager implementations.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(kwargs)

    if any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    ):
        return dict(kwargs)

    accepted = {
        name for name, p in sig.parameters.items()
        if p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return {k: v for k, v in kwargs.items() if k in accepted}


# ============================================================================
# Result normalization
# ============================================================================

def _is_allowed(result: Any) -> bool:
    """
    Normalize an authorization result to a boolean.

    Handles every return style observed in PAI's SecurityManager
    implementations:

      - ``True`` / ``False``              — direct boolean
      - an object with ``.allowed`` / ``.granted`` / ``.authorized``
                                          — dataclass, Pydantic, Enum
      - ``None``                          — the method already raised on
                                            denial and returned normally on
                                            success; treat absence of
                                            exception as "allowed"

    Anything uninterpretable is treated as **allowed** — an `authorize`
    method that returned without raising has already made its decision;
    the return value is a convenience.
    """
    if isinstance(result, bool):
        return result
    if result is None:
        return True
    for attr in ("allowed", "granted", "authorized", "is_allowed", "ok"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "allow", "allowed", "granted")
            if hasattr(value, "value"):  # Enum with string value
                return str(value.value).lower() in (
                    "true", "allow", "allowed", "granted",
                )
    return True


def _denial_reason(result: Any) -> Optional[str]:
    """
    Extract a human-readable reason from a denied authorization result.

    When `SecurityManager` returns `AuthorizationResult(allowed=False,
    reason="plugin requires camera permission")`, this surfaces the
    reason into the AuthorizationError message and the trace.
    """
    if result is None or isinstance(result, bool):
        return None
    for attr in ("reason", "message", "detail", "error"):
        if hasattr(result, attr):
            val = getattr(result, attr)
            if val:
                return str(val)
    return None


# ============================================================================
# AuthorizationManager
# ============================================================================

class AuthorizationManager(Manager):

    def __init__(
        self,
        event_bus:EventBus,
        security_manager: Any,
        plugin_manager: Any = None,
        device_manager: Any = None,
    ):
        super().__init__(event_bus=event_bus)
        self.security = security_manager
        self.plugin_manager = plugin_manager
        self.device_manager = device_manager

    async def _on_initialize(self) -> None:
        if self.security is None:
            raise RuntimeError("AuthorizationManager: security_manager not configured")
        if self.plugin_manager is None:
            raise RuntimeError("AuthorizationManager: plugin_manager not configured")
        if self.device_manager is None:
            raise RuntimeError("AuthorizationManager: device_manager not configured")
    
    async def _on_shutdown(self) -> None:
        logger.info("AuthorizationManager shutdown complete")
        
    async def get_status(self,*, user_id: Optional[str] = None) -> dict:
        return {
            "security_manager": getattr(self.security, "get_status", lambda: {})(),
            "plugin_manager": getattr(self.plugin_manager, "get_status", lambda: {})(),
            "device_manager": getattr(self.device_manager, "get_status", lambda: {})(),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authorize(
        self,
        *,
        user_id: str,
        capability: str,
        device_id: Optional[str] = None,
        plugin_name: Optional[str] = None,
        task: Optional[dict] = None,
    ) -> AuthorizationDecision:

        tracer = get_tracer()
        async with tracer.span(
            "authz.authorize",
            user_id=user_id,
            capability=capability,
            device_id=device_id,
            plugin=plugin_name,
        ) as span:

            # ---- 1. Resolve plugin ----
            async with tracer.span(
                "authz.resolve_plugin",
                capability=capability,
                pre_supplied=plugin_name is not None,
            ) as res_span:
                if plugin_name is None:
                    plugin_name = await self._resolve_plugin(capability)
                res_span.set_attribute("plugin", plugin_name)

            if not plugin_name:
                span.set_error("no_plugin")
                raise AuthorizationError(
                    f"No plugin provides capability '{capability}'"
                )

            span.set_attribute("plugin", plugin_name)

            # ---- 2. Plugin enablement for user ----
            async with tracer.span(
                "authz.check_plugin_enabled",
                user_id=user_id, plugin=plugin_name,
            ) as enabled_span:
                enabled = await self._plugin_enabled(user_id, plugin_name)
                enabled_span.set_attribute("enabled", enabled)

            if not enabled:
                span.set_error("plugin_disabled")
                raise AuthorizationError(
                    f"Plugin '{plugin_name}' is disabled for user '{user_id}'"
                )

            # ---- 3. Security policy ----
            async with tracer.span(
                "authz.check_security",
                user_id=user_id,
                capability=capability,
                plugin=plugin_name,
                device_id=device_id,
            ) as sec_span:
                try:
                    await self._check_security(
                        user_id=user_id,
                        capability=capability,
                        device_id=device_id,
                        plugin_name=plugin_name,
                        task=task,
                        span=sec_span,
                    )
                    sec_span.set_attribute("allowed", True)
                except AuthorizationError as exc:
                    sec_span.set_attribute("allowed", False)
                    sec_span.set_error(str(exc))
                    span.set_error(f"security_denied: {exc}")
                    raise

            # ---- 4. Device ownership / online ----
            if device_id is not None:
                async with tracer.span(
                    "authz.check_device",
                    user_id=user_id,
                    device_id=device_id,
                    capability=capability,
                ) as dev_span:
                    try:
                        await self._check_device(
                            user_id=user_id,
                            device_id=device_id,
                            capability=capability,
                            span=dev_span,
                        )
                        dev_span.set_attribute("allowed", True)
                    except AuthorizationError as exc:
                        dev_span.set_attribute("allowed", False)
                        dev_span.set_error(str(exc))
                        span.set_error(f"device_denied: {exc}")
                        raise

            logger.debug(
                "Authorized capability={} plugin={} device={}",
                capability, plugin_name, device_id,
            )

            return AuthorizationDecision(
                allowed=True,
                capability=capability,
                plugin=plugin_name,
                device_id=device_id,
            )

    # ------------------------------------------------------------------
    # Plugin resolution
    # ------------------------------------------------------------------

    async def _resolve_plugin(self, capability: str) -> Optional[str]:
        """Return the plugin *id* (string) for a capability, or None."""
        if self.plugin_manager is None:
            return None

        method = getattr(self.plugin_manager, "get_plugin_for_capability", None)
        if method:
            result = await method(capability)
            if result is None:
                return None
            return getattr(result, "id", None) or (
                result if isinstance(result, str) else None
            )

        registry = getattr(self.plugin_manager, "registry", None)
        if registry is not None:
            method = getattr(registry, "get_plugin_for_capability", None)
            if method:
                result = await method(capability)
                if result is None:
                    return None
                return getattr(result, "id", None) or (
                    result if isinstance(result, str) else None
                )

        plugins = getattr(self.plugin_manager, "plugins", {}) or {}
        for plugin in plugins.values():
            caps = getattr(plugin, "capabilities", []) or []
            if capability in caps:
                return getattr(plugin, "id", None) or getattr(plugin, "name", None)

        return None

    async def _plugin_enabled(self, user_id: str, plugin_name: str) -> bool:
        """True iff the plugin is enabled for the user (or globally fallback)."""
        if self.plugin_manager is None:
            return False

        method = getattr(self.plugin_manager, "is_enabled_for_user", None)
        if method:
            return bool(await method(user_id, plugin_name))

        for name in ("is_enabled", "is_enabled_for_user"):
            method = getattr(self.plugin_manager, name, None)
            if method:
                try:
                    return bool(await method(user_id, plugin_name))
                except TypeError:
                    continue

        enabled_plugins = getattr(self.plugin_manager, "enabled_plugins", None)
        if enabled_plugins is not None:
            return plugin_name in enabled_plugins

        logger.warning(
            "AuthorizationManager: cannot determine enablement for "
            "user={} plugin={}; defaulting to allow (router already validated)",
            user_id, plugin_name,
        )
        return True

    # ------------------------------------------------------------------
    # Security check
    # ------------------------------------------------------------------

    async def _check_security(
        self,
        *,
        user_id: str,
        capability: str,
        device_id: Optional[str],
        plugin_name: str,
        task: Optional[dict],
        span: Any = None,
    ) -> None:
        """
        Call SecurityManager.authorize (or check_permission) with
        introspection-based kwarg filtering.

        Sets rich attributes on `span` — the SecurityManager's actual
        signature, the kwargs it received, the decision, and the denial
        reason if any. This is the layer that produced the last failure
        in the trace, so visibility here matters more than anywhere
        else.
        """
        if self.security is None:
            raise AuthorizationError("Security manager is not configured")

        method = getattr(self.security, "authorize", None)
        if method is not None:
            # Offer every plausible kwarg name — different
            # SecurityManager implementations use `plugin`,
            # `plugin_name`, and `plugin_id` interchangeably.
            all_kwargs = {
                "user_id":     user_id,
                "capability":  capability,
                "device_id":   device_id,
                "plugin":      plugin_name,
                "plugin_name": plugin_name,
                "plugin_id":   plugin_name,
                "task":        task,
            }
            kwargs = _call_kwargs_for(method, all_kwargs)

            # ---- Trace what we're about to send ----
            if span is not None:
                try:
                    span.set_attribute(
                        "security_signature",
                        str(inspect.signature(method)),
                    )
                except (TypeError, ValueError):
                    pass
                span.set_attribute(
                    "security_kwargs", sorted(kwargs.keys()),
                )

            try:
                result = await method(**kwargs)
            except TypeError as exc:
                # Introspection filtered kwargs but the call still
                # failed — surface the signature so this is diagnosable
                # from the trace alone.
                try:
                    sig_str = str(inspect.signature(method))
                except (TypeError, ValueError):
                    sig_str = "<uninspectable>"
                raise AuthorizationError(
                    f"SecurityManager.authorize() signature mismatch "
                    f"({exc}). Actual signature: {sig_str}"
                ) from exc

            # ---- Normalize the return type ----
            allowed = _is_allowed(result)

            # ---- Trace the decision details ----
            if span is not None:
                span.set_attribute("security_allowed", allowed)
                # Surface risk/required_permissions if present
                for attr in ("risk", "required_permissions", "permissions"):
                    if hasattr(result, attr):
                        val = getattr(result, attr)
                        if val is not None:
                            span.set_attribute(f"security_{attr}", str(val))

            if not allowed:
                reason = _denial_reason(result)
                if span is not None:
                    span.set_attribute(
                        "security_denial_reason", reason or "<unspecified>",
                    )
                raise AuthorizationError(
                    f"Security policy denied '{capability}'"
                    + (f": {reason}" if reason else "")
                )
            return

        # Fallback: check_permission style manager
        method = getattr(self.security, "check_permission", None)
        if method is not None:
            all_kwargs = {
                "user_id":     user_id,
                "capability":  capability,
                "device_id":   device_id,
                "plugin":      plugin_name,
                "plugin_name": plugin_name,
                "plugin_id":   plugin_name,
            }
            kwargs = _call_kwargs_for(method, all_kwargs)

            if span is not None:
                span.set_attribute(
                    "security_kwargs", sorted(kwargs.keys()),
                )

            result = await method(**kwargs)
            allowed = _is_allowed(result)

            if span is not None:
                span.set_attribute("security_allowed", allowed)

            if not allowed:
                reason = _denial_reason(result)
                if span is not None:
                    span.set_attribute(
                        "security_denial_reason", reason or "<unspecified>",
                    )
                raise AuthorizationError(
                    f"Security policy denied '{capability}'"
                    + (f": {reason}" if reason else "")
                )
            return

        raise AuthorizationError(
            "SecurityManager does not expose an authorization API"
        )

    # ------------------------------------------------------------------
    # Device check — mirrors the security check
    # ------------------------------------------------------------------

    async def _check_device(
        self,
        *,
        user_id: str,
        device_id: str,
        capability: str,
        span: Any = None,
    ) -> None:
        """
        Verify the target device belongs to the user and is online.

        Uses `_is_allowed` for the same return-type tolerance as the
        security check — DeviceManager.authorize (if it exists) may
        return a bool, an AuthorizationResult, or None.
        """
        if self.device_manager is None:
            raise AuthorizationError("Device manager is not configured")

        method = getattr(self.device_manager, "authorize", None)
        if method is not None:
            all_kwargs = {
                "user_id":    user_id,
                "device_id":  device_id,
                "capability": capability,
            }
            kwargs = _call_kwargs_for(method, all_kwargs)
            result = await method(**kwargs)
            allowed = _is_allowed(result)
            if span is not None:
                span.set_attribute("device_allowed", allowed)
            if not allowed:
                reason = _denial_reason(result)
                raise AuthorizationError(
                    f"Device '{device_id}' is not authorized"
                    + (f": {reason}" if reason else "")
                )
            return

        # Manual check — inspect the DeviceInfo record
        device = await self.device_manager.get(device_id)
        if device is None:
            if span is not None:
                span.set_attribute("device_found", False)
            raise AuthorizationError(f"Unknown device '{device_id}'")

        owner = getattr(device, "user_id", None)
        online = getattr(device, "online", None)
        if online is None:
            online = getattr(device, "is_online", True)

        # Trace every fact used in the decision
        if span is not None:
            span.set_attribute("device_found", True)
            span.set_attribute("device_owner", owner)
            span.set_attribute("device_user_id", user_id)
            span.set_attribute("device_owner_matches", owner == user_id if owner else True)
            span.set_attribute("device_online", bool(online))
            span.set_attribute(
                "device_status", str(getattr(device, "status", "unknown")),
            )

        if owner and owner != user_id:
            raise AuthorizationError(
                f"Device '{device_id}' belongs to another user"
            )

        if not online:
            raise AuthorizationError(f"Device '{device_id}' is offline")