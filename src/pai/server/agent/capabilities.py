"""Capabilities implemented natively by the agent runtime.

Mirrors the built-in handler in the Flutter app (`_runBuiltinOrDispatch`)
so every device runtime responds identically to the same invoke frame.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from pai.server.agent.notifications import show_notification

logger = logging.getLogger(__name__)

Handler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class BuiltinCapabilities:
    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {
            "system.notifications.show": self._notification_show,
        }

    def handles(self, capability: str) -> bool:
        return capability in self._handlers

    def list(self) -> list[str]:
        return sorted(self._handlers)

    async def invoke(
        self,
        capability: str,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        handler = self._handlers.get(capability)
        if handler is None:
            raise ValueError(f"Unknown built-in capability: {capability!r}")
        logger.debug(
            "Built-in capability %s params=%s", capability, parameters,
        )
        return await handler(parameters)

    # ------------------------------------------------------------------

    async def _notification_show(
        self, params: Dict[str, Any],
    ) -> Dict[str, Any]:
        title = str(params.get("title") or "PAI reminder")
        body = str(params.get("body") or "")
        urgency = str(params.get("urgency") or "normal").lower()
        if urgency not in {"low", "normal", "critical"}:
            urgency = "normal"

        shown = show_notification(title, body, urgency=urgency)
        if not shown:
            # No silent failures — raise so the backend marks the task
            # failed with a real reason instead of a phantom success.
            raise RuntimeError(
                "OS notification could not be displayed — "
                "check that notify-send / libnotify is installed"
            )

        return {"shown": True, "title": title, "body": body}