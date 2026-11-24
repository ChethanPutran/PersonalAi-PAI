"""Executor that runs built-in capabilities in-process (no device)."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from typing import Any, Dict

from loguru import logger

from pai.executors.base import BaseExecutor
from pai.orchestration.builtin_capabilities import BuiltinCapabilityRegistry


def _accepts_pause(handler: Any) -> bool:
    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False
    return "pause" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class PauseGate:
    """Cooperative pause for in-process capabilities.

    The handler calls ``await pause.checkpoint()`` between units of work; while the user has the
    task paused the call blocks there, and continues on resume. A capability that never calls it
    must not declare ``controls.pause`` (the API would then refuse the pause honestly).
    """

    def __init__(self) -> None:
        self._running = asyncio.Event()
        self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    async def checkpoint(self) -> None:
        await self._running.wait()


class BuiltinExecutor(BaseExecutor):
    def __init__(self, registry: BuiltinCapabilityRegistry) -> None:
        super().__init__(
            name="builtin_executor",
            executor_id=str(uuid.uuid4()),
        )
        self.registry = registry
        self._gates: Dict[str, PauseGate] = {}      # task_id -> gate of the call in flight

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
                "retryable": False,       # a missing capability will still be missing next time
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

        gate = PauseGate()
        if task_id:
            self._gates[task_id] = gate
        try:
            kwargs: Dict[str, Any] = {"user_id": user_id, "task": task}
            if _accepts_pause(cap.handler):      # handlers written before pause support don't take it
                kwargs["pause"] = gate
            result = await cap.handler(params, **kwargs)
        except Exception as exc:
            logger.exception("BuiltinExecutor: {} failed", cap_id)
            return {
                "status": "failed",
                "capability": cap_id,
                "error": f"{type(exc).__name__}: {exc}",
                # Bad input fails identically every time: do not back off and retry it.
                "retryable": not isinstance(exc, (ValueError, TypeError, KeyError)),
            }

        finally:
            if task_id:
                self._gates.pop(task_id, None)

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
    # Task control
    # ------------------------------------------------------------------
    # Cancel needs nothing here: the task manager cancels the awaiting coroutine, which
    # interrupts the handler at its next await. Pause is cooperative through the gate
    # (only meaningful for capabilities that declare controls.pause and call checkpoint()).

    async def cancel_task(self, task_id: str) -> bool:
        return False

    async def pause_task(self, task_id: str) -> bool:
        gate = self._gates.get(task_id)
        if gate is None:
            return False
        gate.pause()
        return True

    async def resume_task(self, task_id: str) -> bool:
        gate = self._gates.get(task_id)
        if gate is None:
            return False
        gate.resume()
        return True