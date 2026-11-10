"""Capability resolution — task → plugin + viable targets."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_router import (
    CapabilityNotAvailable,
    CapabilityRouter,
    CapabilityTarget,
)
from pai.plugins.manager import PluginManager


@dataclass(frozen=True)
class CapabilityResolution:
    capability: str
    plugin_name: str
    targets: list[CapabilityTarget] = field(default_factory=list)
    provider: Any = None



class CapabilityResolver(Lifecycle):

    def __init__(
        self,
        plugin_manager: PluginManager,
        capability_router: CapabilityRouter,
    ):
        self.plugin_manager = plugin_manager
        self.capability_router = capability_router

    def is_initialized(self) -> bool:
        return (
            self.plugin_manager.is_initialized()
            and self.capability_router.is_initialized()
        )

    async def shutdown(self) -> None:
        logger.info("CapabilityResolver shutdown complete")


    async def initialize(self) -> None:
        if not self.plugin_manager.is_initialized():
            raise RuntimeError("CapabilityResolver: plugin_manager not initialized")
        if not self.capability_router.is_initialized():
            raise RuntimeError("CapabilityResolver: capability_router not initialized")
        logger.info(
            "CapabilityResolver initialized ({} plugins in catalog)",
            len(self.plugin_manager.get_catalog()),
        )

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "plugin_manager": self.plugin_manager.get_status(user_id=user_id) ,
            "capability_router": self.capability_router.get_status(user_id=user_id),
        }

    async def resolve(
        self,
        task: Any,
        *,
        user_id: str,
    ) -> CapabilityResolution:
        tracer = get_tracer()

        task_dict = (
            task.to_dict() if hasattr(task, "to_dict") else dict(task)
        )
        task_id = task_dict.get("id")
        capability = task_dict.get("capability") or task_dict.get("type")

        async with tracer.span(
            "resolver.resolve",
            task_id=task_id,
            capability=capability,
            user_id=user_id,
        ) as span:

            if not capability:
                span.set_error("missing_capability")
                raise RuntimeError("Task does not specify a capability")

            try:
                targets = await self.capability_router.resolve(
                    capability=capability,
                    user_id=user_id,
                    preferred_device_id=task_dict.get("preferred_device"),
                )
            except CapabilityNotAvailable as exc:
                span.set_error(
                    f"unavailable: {exc.error.detail.get('stage')}"
                )
                span.set_attribute("reason", exc.error.reason)
                logger.warning(
                    "Resolver: capability={} unavailable for user={}: {}",
                    capability, user_id, exc.error.reason,
                )
                raise

            if not targets:
                # Defensive — router should already raise
                span.set_error("no_targets")
                raise RuntimeError(
                    f"Capability '{capability}' resolved to no targets"
                )

            primary = targets[0]
            span.set_attribute("plugin", primary.plugin_id)
            span.set_attribute("target_count", len(targets))
            span.set_attribute(
                "chosen_device",
                {"device_id": primary.device_id,
                 "executor_id": primary.executor_id},
            )

            logger.debug(
                "Resolver: {} → plugin={} ({} targets, chosen={})",
                capability, primary.plugin_id, len(targets),
                primary.device_id,
            )

            return CapabilityResolution(
                capability=capability,
                plugin_name=primary.plugin_id,
                targets=targets,
            )