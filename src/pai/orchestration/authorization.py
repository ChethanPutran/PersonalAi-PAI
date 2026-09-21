"""
Authorization boundary for orchestration.

No executor should be reached before this layer succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger


class AuthorizationError(PermissionError):
    """Raised when an orchestration action is not authorized."""


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    capability: str
    plugin: Optional[str] = None
    device_id: Optional[str] = None
    reason: Optional[str] = None


class AuthorizationManager:
    def __init__(
        self,
        security_manager: Any,
        plugin_manager: Any = None,
        device_manager: Any = None,
    ):
        self.security = security_manager
        self.plugin_manager = plugin_manager
        self.device_manager = device_manager


    def get_status(self) -> dict:
        return {
            "security_manager": getattr(self.security, "get_status", lambda: {})(),
            "plugin_manager": getattr(self.plugin_manager, "get_status", lambda: {})(),
            "device_manager": getattr(self.device_manager, "get_status", lambda: {})(),
        }
    
    async def authorize(
        self,
        *,
        user_id: str,
        capability: str,
        device_id: Optional[str] = None,
        plugin_name: Optional[str] = None,
        task: Optional[dict] = None,
    ) -> AuthorizationDecision:

        # ---------------------------------------------------------
        # 1. Resolve plugin
        # ---------------------------------------------------------

        if plugin_name is None:
            plugin_name = await self._resolve_plugin(
                capability
            )

        if not plugin_name:
            raise AuthorizationError(
                f"No plugin provides capability '{capability}'"
            )

        # ---------------------------------------------------------
        # 2. Check whether plugin is enabled for the user
        # ---------------------------------------------------------

        enabled = await self._plugin_enabled(
            user_id,
            plugin_name,
        )

        if not enabled:
            raise AuthorizationError(
                f"Plugin '{plugin_name}' is disabled for user "
                f"'{user_id}'"
            )

        # ---------------------------------------------------------
        # 3. Security permission
        # ---------------------------------------------------------

        await self._check_security(
            user_id=user_id,
            capability=capability,
            device_id=device_id,
            plugin_name=plugin_name,
            task=task,
        )

        # ---------------------------------------------------------
        # 4. Device authorization
        # ---------------------------------------------------------

        if device_id is not None:
            await self._check_device(
                user_id=user_id,
                device_id=device_id,
                capability=capability,
            )

        logger.debug(
            "Authorized capability={} plugin={} device={}",
            capability,
            plugin_name,
            device_id,
        )

        return AuthorizationDecision(
            allowed=True,
            capability=capability,
            plugin=plugin_name,
            device_id=device_id,
        )

    async def _resolve_plugin(
        self,
        capability: str,
    ) -> Optional[str]:

        if self.plugin_manager is None:
            return None

        registry = getattr(
            self.plugin_manager,
            "registry",
            None,
        )

        if registry is not None:
            method = getattr(
                registry,
                "get_plugin_for_capability",
                None,
            )

            if method:
                return await method(capability)

        method = getattr(
            self.plugin_manager,
            "get_plugin_for_capability",
            None,
        )

        if method:
            return await method(capability)

        # Fallback for plugin managers exposing loaded plugins.
        plugins = getattr(
            self.plugin_manager,
            "plugins",
            {},
        )

        for plugin in plugins.values():
            capabilities = getattr(
                plugin,
                "capabilities",
                [],
            )

            if capability in capabilities:
                return getattr(plugin, "name", None)

        return None

    async def _plugin_enabled(
        self,
        user_id: str,
        plugin_name: str,
    ) -> bool:

        if self.plugin_manager is None:
            return False

        # User-specific plugin enablement.
        method = getattr(
            self.plugin_manager,
            "is_enabled_for_user",
            None,
        )

        if method:
            return bool(
                await method(
                    user_id,
                    plugin_name,
                )
            )

        method = getattr(
            self.plugin_manager,
            "is_enabled",
            None,
        )

        if method:
            return bool(
                await method(
                    user_id,
                    plugin_name,
                )
            )

        # Global plugin enablement fallback.
        enabled_plugins = getattr(
            self.plugin_manager,
            "enabled_plugins",
            None,
        )

        if enabled_plugins is not None:
            return plugin_name in enabled_plugins

        return False

    async def _check_security(
        self,
        *,
        user_id: str,
        capability: str,
        device_id: Optional[str],
        plugin_name: str,
        task: Optional[dict],
    ) -> None:

        if self.security is None:
            raise AuthorizationError(
                "Security manager is not configured"
            )

        # Preferred API.
        method = getattr(
            self.security,
            "authorize",
            None,
        )

        if method:
            allowed = await method(
                user_id=user_id,
                capability=capability,
                device_id=device_id,
                plugin=plugin_name,
                task=task,
            )

            if allowed is False:
                raise AuthorizationError(
                    f"Security policy denied '{capability}'"
                )

            return

        # Compatibility with a check_permission style manager.
        method = getattr(
            self.security,
            "check_permission",
            None,
        )

        if method:
            allowed = await method(
                user_id=user_id,
                capability=capability,
                device_id=device_id,
            )

            if allowed is False:
                raise AuthorizationError(
                    f"Security policy denied '{capability}'"
                )

            return

        raise AuthorizationError(
            "SecurityManager does not expose an authorization API"
        )

    async def _check_device(
        self,
        *,
        user_id: str,
        device_id: str,
        capability: str,
    ) -> None:

        if self.device_manager is None:
            raise AuthorizationError(
                "Device manager is not configured"
            )

        method = getattr(
            self.device_manager,
            "authorize",
            None,
        )

        if method:
            allowed = await method(
                user_id=user_id,
                device_id=device_id,
                capability=capability,
            )

            if allowed is False:
                raise AuthorizationError(
                    f"Device '{device_id}' is not authorized"
                )

            return

        # At minimum verify device exists and is online.
        device = await self.device_manager.get(
            device_id
        )

        if device is None:
            raise AuthorizationError(
                f"Unknown device '{device_id}'"
            )

        online = getattr(device, "online", None)

        if online is None:
            online = getattr(
                device,
                "is_online",
                True,
            )

        if not online:
            raise AuthorizationError(
                f"Device '{device_id}' is offline"
            )