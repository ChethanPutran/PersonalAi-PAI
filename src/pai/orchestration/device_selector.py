"""Device selection — picks the best target for a capability."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_router import (
    CapabilityRouter,
    CapabilityTarget,
)


class DeviceSelector(Lifecycle):

    def __init__(
        self,
        device_manager,
        executor_manager,
        capability_router: CapabilityRouter,
    ):
        self.device_manager = device_manager
        self.executor_manager = executor_manager
        self.capability_router = capability_router

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "capability_router": await self.capability_router.get_status(user_id=user_id),
        }

    def is_initialized(self) -> bool:
        return self.capability_router.is_initialized()

    async def initialize(self) -> None:
        await self.capability_router.initialize()
        logger.info("DeviceSelector: initialized")
    async def shutdown(self) -> None:
        await self.capability_router.shutdown()
        logger.info("DeviceSelector: shutdown complete")
        
    async def select(
        self,
        *,
        user_id: str,
        capability: str,
        task: dict,
        preferred_device: Optional[str] = None,
        targets: Optional[List[CapabilityTarget]] = None,
    ) -> CapabilityTarget:
        """Choose a target for capability.

        If [targets] is provided, uses them directly (avoids a redundant
        router call when the caller already resolved). Otherwise, queries
        the router.
        """
        tracer = get_tracer()

        async with tracer.span(
            "selector.select",
            user_id=user_id,
            capability=capability,
            preferred_device=preferred_device,
            pre_resolved=targets is not None,
        ) as span:

            if targets is None:
                targets = await self.capability_router.resolve(
                    capability=capability,
                    user_id=user_id,
                    preferred_device_id=(
                        preferred_device or task.get("preferred_device")
                    ),
                )

            if not targets:
                span.set_error("no_targets")
                raise RuntimeError(
                    f"No device targets available for capability "
                    f"'{capability}' (user={user_id})"
                )

            # Candidates already ranked by the router; pick the best
            chosen = targets[0]

            span.set_attribute(
                "chosen",
                {
                    "device_id": chosen.device_id,
                    "executor_id": chosen.executor_id,
                    "plugin_id": chosen.plugin_id,
                    "device_type": chosen.device_type,
                    "score": chosen.score,
                    "is_preferred": chosen.is_preferred,
                },
            )
            span.set_attribute("candidate_count", len(targets))

            logger.debug(
                "DeviceSelector: {} → device={} executor={} (plugin={}, "
                "score={:.1f}, preferred={})",
                capability,
                chosen.device_id,
                chosen.executor_id,
                chosen.plugin_id,
                chosen.score,
                chosen.is_preferred,
            )

            return chosen