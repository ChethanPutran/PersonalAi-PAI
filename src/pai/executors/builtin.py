"""Executor that runs built-in capabilities in-process (no device)."""
from __future__ import annotations

import uuid
from typing import Any, Dict

from loguru import logger

from pai.executors.base import BaseExecutor
from pai.orchestration.builtin_capabilities import BuiltinCapabilityRegistry


class BuiltinExecutor(BaseExecutor):
    def __init__(self, registry: BuiltinCapabilityRegistry) -> None:
        super().__init__(
            name="builtin_executor",
            executor_id=str(uuid.uuid4()),
        )
        self.registry = registry

    # ------------------------------------------------------------------
    # BaseExecutor contract
    # ------------------------------------------------------------------

    async def execute_task(self, task: Any) -> Dict[str, Any]:
        """Entry point called by ExecutorScheduler."""
        cap_id = (
            getattr(task, "capability", None)
            or getattr(task, "type", None)
            or (task.get("capability") if isinstance(task, dict) else None)
            or (task.get("type") if isinstance(task, dict) else None)
        )
        if not cap_id:
            return {
                "status": "failed",
                "error": "task has no capability",
            }

        cap = self.registry.get(cap_id)
        if cap is None:
            return {
                "status": "failed",
                "error": (
                    f"No built-in capability '{cap_id}'. Known: "
                    f"{sorted(c.id for c in self.registry.list_all())}"
                ),
            }

        # TaskManager always hands us a Task model, but be defensive
        # in case a raw dict slips in from tests.
        params = (
            getattr(task, "parameters", None)
            if hasattr(task, "parameters")
            else (task.get("parameters") if isinstance(task, dict) else {})
        ) or {}
        user_id = (
            getattr(task, "user_id", None)
            if hasattr(task, "user_id")
            else (task.get("user_id") if isinstance(task, dict) else None)
        )
        task_id = (
            getattr(task, "id", None)
            if hasattr(task, "id")
            else (task.get("id") if isinstance(task, dict) else None)
        )

        logger.info(
            "BuiltinExecutor: {} task={} user={} params={}",
            cap_id, task_id, user_id, params,
        )

        try:
            result = await cap.handler(
                params, user_id=user_id, task=task,
            )
        except Exception as exc:
            logger.exception("BuiltinExecutor: {} failed", cap_id)
            return {
                "status": "failed",
                "capability": cap_id,
                "error": f"{type(exc).__name__}: {exc}",
            }

        return {
            "status": "success",
            "capability": cap_id,
            "output": result,
        }

    async def invoke(
        self,
        device_id: str,
        capability: str,
        parameters: Dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """Built-ins are never invoked via device_id — they run in-process.

        This method exists only to satisfy BaseExecutor's ABC. If it's
        ever called, that's a routing bug in the router (it shouldn't
        have put a built-in behind a device target). Raise loudly.
        """
        raise NotImplementedError(
            f"BuiltinExecutor.invoke() called for {capability!r} — "
            f"built-in capabilities route via execute_task, not invoke. "
            f"Check CapabilityRouter: built-ins must have "
            f"requires_device=False and device_id='server'."
        )

    # ------------------------------------------------------------------
    # Task control — no-ops (built-ins are atomic)
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str) -> bool:
        return False

    async def pause_task(self, task_id: str) -> bool:
        return False

    async def resume_task(self, task_id: str) -> bool:
        return False