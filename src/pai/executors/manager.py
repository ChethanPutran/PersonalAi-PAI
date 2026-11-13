"""
Executor registry and lifecycle manager.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.executors.base import BaseExecutor
from pai.observability.tracer import get_tracer
from pai.common.manager import Manager


class ExecutorManager(Manager):
    """
        Manages registered executors.
    
        Responsibilities:
        - register
        - unregister
        - lookup
        - initialize
        - start
        - stop
        - shutdown
        """
    name = "executor_manager"
    
    def __init__(self, *, event_bus, **kwargs):
        super().__init__(event_bus=event_bus)
        self._executors: Dict[str, BaseExecutor] = {}


    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def register(self, executor: BaseExecutor) -> None:
        tracer = get_tracer()
        with tracer.span(
            "executor_manager.register",
            executor_name=executor.name,
            executor_type=executor.__class__.__name__,
        ):
            if executor.name in self._executors:
                raise ValueError(
                    f"Executor already registered: {executor.name}"
                )

            self._executors[executor.name] = executor
            self._emit("executor.registered", {
                "name": executor.name,
                "type": executor.__class__.__name__,
                "id": executor.executor_id,
            })
            logger.info("Executor registered: {}", executor.name)


    def unregister(
        self,
        executor_name: str,
    ) -> Optional[BaseExecutor]:
        tracer = get_tracer()
        with tracer.span(
            "executor_manager.unregister",
            executor_name=executor_name,
        ):
            executor = self._executors.pop(executor_name, None)
            self._emit("executor.unregistered", {
                "name": executor_name,
                "type": executor.__class__.__name__ if executor else None,
                "id": executor.executor_id if executor else None,
            })
            if executor:
                logger.info("Executor unregistered: {}", executor_name)
            return executor

    def get(self, executor_name: str) -> Optional[BaseExecutor]:
        return self._executors.get(executor_name)

    def require(self, executor_name: str) -> BaseExecutor:
        executor = self.get(executor_name)
        if executor is None:
            raise KeyError(f"Executor not found: {executor_name}")
        return executor

    def list(self) -> List[BaseExecutor]:
        return list(self._executors.values())

    def names(self) -> List[str]:
        return list(self._executors.keys())

    # ------------------------------------------------------------------
    # Task lifecycle delegation
    # ------------------------------------------------------------------
    async def pause_task(self, executor_id: str, task_id: str) -> bool:
        executor = self.get(executor_id)
        if executor is None:
            logger.warning("pause_task: unknown executor {}", executor_id)
            return False
        try:
            return bool(await executor.pause_task(task_id))
        except Exception as exc:
            logger.warning("pause_task failed for {}/{}: {}", executor_id, task_id, exc)
            return False

    async def resume_task(self, executor_id: str, task_id: str) -> bool:
        executor = self.get(executor_id)
        if executor is None:
            logger.warning("resume_task: unknown executor {}", executor_id)
            return False
        try:
            return bool(await executor.resume_task(task_id))
        except Exception as exc:
            logger.warning("resume_task failed for {}/{}: {}", executor_id, task_id, exc)
            return False

    async def cancel_task(self, executor_id: str, task_id: str) -> bool:
        executor = self.get(executor_id)
        if executor is None:
            logger.warning("cancel_task: unknown executor {}", executor_id)
            return False
        try:
            return bool(await executor.cancel_task(task_id))
        except Exception as exc:
            logger.warning("cancel_task failed for {}/{}: {}", executor_id, task_id, exc)
            return False
    
    # ------------------------------------------------------------------
    # Result routing
    # ------------------------------------------------------------------

    def deliver_result(self, request_id: str, payload: Dict[str, Any]) -> bool:
        """
        Route a `result` frame to whichever registered executor has a
        pending request for `request_id`.

        Returns True if an executor claimed the frame, False if none did
        (e.g. the request already timed out, or the device reconnected
        before the frame arrived).

        Every executor that implements `deliver_result` is asked in
        registration order. That's fine — the contract is that
        `deliver_result` returns True only when it actually owned a
        future for this `request_id`, and False otherwise, so at most
        one executor ever returns True.
        """
        for executor in self._executors.values():
            deliver = getattr(executor, "deliver_result", None)
            if deliver is None:
                continue
            try:
                if deliver(request_id, payload):
                    logger.debug(
                        "Delivered result {} to {}", request_id, executor.name,
                    )
                    return True
            except Exception as exc:
                logger.warning(
                    "deliver_result on {} failed for {}: {}",
                    executor.name, request_id, exc,
                )

        logger.debug(
            "No executor claimed request_id={} "
            "(was the device already disconnected or the request timed out?)",
            request_id,
        )
        return False
    
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _on_initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "executor_manager.initialize_all",
            executor_count=len(self._executors),
        ) as span:
            failures = []
            for name, executor in self._executors.items():
                async with tracer.span(
                    "executor_manager.init_one",
                    executor_name=name,
                ) as one_span:
                    try:
                        # Hard cap: 5 seconds per executor. Android ADB
                        # can block indefinitely on a machine without an
                        # SDK — that shouldn't hold up the whole server.
                        await asyncio.wait_for(
                            executor.initialize(),
                            timeout=5.0,
                        )
                    except asyncio.TimeoutError as e:
                        self._emit(
                            "executor.failed",
                            {
                                "name": executor.name,
                                "error": str(e),
                                "stage":"initialize"
                            }
                        )
                        logger.warning(
                            "Executor {} initialization timed out "
                            "(5s) — disabling it for this run",
                            name,
                        )
                        one_span.set_error("timeout")
                        one_span.set_attribute("disabled", True)
                        # Mark it not-running so schedule_task skips it
                        try:
                            executor._running = False
                        except Exception:
                            pass
                        failures.append(name)
                    except Exception as exc:
                        self._emit(
                                "executor.failed",
                                {
                                    "name": executor.name,
                                    "error": str(exc),
                                    "stage":"initialize"
                                }
                                                )
                        logger.exception(
                            "Failed to initialize executor: {}", name,
                        )
                        one_span.set_error(f"{type(exc).__name__}: {exc}")
                        failures.append(name)
            span.set_attribute("failures", failures)

    async def start_all(self) -> None:
        for name, executor in self._executors.items():
            # Skip executors that failed to initialize
            if not getattr(executor, "initialized", True):
                logger.debug("Skipping start for uninitialized {}", name)
                continue
            try:
                await asyncio.wait_for(executor.start(), timeout=5.0)
                self._emit("executor.started",{
                    "name": executor.name
                })
            except asyncio.TimeoutError:
                logger.warning("Executor {} start timed out — skipping", name)
                try:
                    executor._running = False
                except Exception:
                    pass
            except Exception:
                logger.exception("Failed to start executor: {}", name)

    async def stop_all(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "executor_manager.stop_all",
            executor_count=len(self._executors),
        ) as span:
            failures = []
            for executor in self._executors.values():
                async with tracer.span(
                    "executor_manager.stop_one",
                    executor_name=executor.name,
                ) as one_span:
                    try:
                        await executor.stop()
                        self._emit("executor.stopped",{
                            "name":executor.name
                        })
                    except Exception as exc:
                        logger.exception(
                            "Failed to stop executor: {}", executor.name,
                        )
                        one_span.set_error(f"{type(exc).__name__}: {exc}")
                        failures.append(executor.name)
            span.set_attribute("failures", failures)

    async def shutdown_all(self) -> None:
        tracer = get_tracer()
        async with tracer.span(
            "executor_manager.shutdown_all",
            executor_count=len(self._executors),
        ) as span:
            failures = []
            for executor in self._executors.values():
                async with tracer.span(
                    "executor_manager.shutdown_one",
                    executor_name=executor.name,
                ) as one_span:
                    try:
                        await executor.shutdown()
                    except Exception as exc:
                        logger.exception(
                            "Failed to shutdown executor: {}", executor.name,
                        )
                        one_span.set_error(f"{type(exc).__name__}: {exc}")
                        failures.append(executor.name)
            span.set_attribute("failures", failures)

    def __contains__(self, executor_name: str) -> bool:
        return executor_name in self._executors

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get the status of the executor manager."""
        return {
            name: await executor.get_status()
            for name, executor in self._executors.items()
        }