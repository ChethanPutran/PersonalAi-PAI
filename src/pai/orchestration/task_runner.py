"""
Task runner used by the planning DAG scheduler.

DAGScheduler gives us a TaskSpec.
TaskRunner converts that declarative TaskSpec into an executable
runtime task through TaskManager.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.event_bus.bus import EventBus
from pai.executors.manager import ExecutorManager
from pai.observability.tracer import get_tracer
from pai.orchestration.authorization import AuthorizationManager
from pai.orchestration.capability_resolver import CapabilityResolver
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.device_selector import DeviceSelector
from pai.planning.models import TaskSpec
from pai.tasks.manager import TaskManager


class TaskRunner(Lifecycle):

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        capability_resolver: CapabilityResolver,
        device_selector: DeviceSelector,
        authorization_manager: AuthorizationManager,
        executor_manager: ExecutorManager,
        event_bus: EventBus,
    ):
        self.task_manager = task_manager
        self.capability_resolver = capability_resolver
        self.device_selector = device_selector
        self.authorizer = authorization_manager
        self.executor_manager = executor_manager
        self.event_bus = event_bus

    async def initialize(self) -> None:
        if not self.capability_resolver.is_initialized():
            raise RuntimeError(
                "TaskRunner: capability_resolver not initialized"
            )
        if not self.device_selector.is_initialized():
            raise RuntimeError("TaskRunner: device_selector not initialized")
        if not self.authorizer.is_initialized():
            raise RuntimeError(
                "TaskRunner: authorization_manager not initialized"
            )
        if not self.executor_manager.is_initialized():
            raise RuntimeError(
                "TaskRunner: executor_manager not initialized"
            )
        if not self.task_manager.is_initialized():
            raise RuntimeError("TaskRunner: task_manager not initialized")

        if self.event_bus and not getattr(
            self.event_bus, "is_initialized", lambda: True
        )():
            raise RuntimeError("TaskRunner: event_bus not initialized")

    def is_initialized(self) -> bool:
        is_initialized = getattr(self.task_manager, "is_initialized", None)
        return bool(is_initialized()) if callable(is_initialized) else False

    async def shutdown(self) -> None:
        logger.info("TaskRunner shutdown complete")
        
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        task: TaskSpec,
        context: Any,
    ) -> Dict[str, Any]:

        task_dict = task.to_dict() 
        task_id = str(task_dict.get("id"))
        capability = task_dict.get("capability") or task_dict.get("type")

        logger.info(
            "Running planned task {} (capability={})",
            task_id, capability,
        )

        tracer = get_tracer()

        async with tracer.span(
            f"task_runner.run:{capability}",
            task_id=task_id,
            capability=capability,
            user_id=context.user_id,
        ) as span:

            # ---- 1. Resolve capability -> plugin + targets ----
            async with tracer.span(
                "task_runner.resolve_capability",
                capability=capability,
            ) as res_span:
                resolution = await self.capability_resolver.resolve(
                    task,
                    user_id=context.user_id,
                )
                res_span.set_attribute("plugin", resolution.plugin_name)
                res_span.set_attribute(
                    "target_count", len(resolution.targets),
                )

            span.set_attribute("plugin", resolution.plugin_name)

            # ---- 2. Select device ----
            preferred = (
                task_dict.get("preferred_device")
                or getattr(task, "preferred_device", None)
            )

            async with tracer.span(
                "task_runner.select_device",
                capability=resolution.capability,
                preferred_device=preferred,
            ) as sel_span:
                target = await self.device_selector.select(
                    user_id=context.user_id,
                    capability=resolution.capability,
                    task=task_dict,
                    preferred_device=preferred,
                    targets=resolution.targets,   # ← reuse, router cache hit avoided
                )
                sel_span.set_attribute("device_id", target.device_id)
                sel_span.set_attribute("executor_id", target.executor_id)

            span.set_attribute("device_id", target.device_id)
            span.set_attribute("executor_id", target.executor_id)

            # ---- 3. Authorize ----
            async with tracer.span(
                "task_runner.authorize",
                capability=resolution.capability,
                device_id=target.device_id,
                plugin=resolution.plugin_name,
            ):
                await self.authorizer.authorize(
                    user_id=context.user_id,
                    capability=resolution.capability,
                    device_id=target.device_id,
                    plugin_name=resolution.plugin_name,
                    task=task_dict,
                )

            # ---- 4. Create runtime task via TaskManager ----
            async with tracer.span(
                "task_runner.create_task",
                capability=resolution.capability,
                device_id=target.device_id,
            ) as create_span:
                runtime_task = await self._create_runtime_task(
                    task_spec=task,
                    context=context,
                    target=target,
                    capability=resolution,
                )
                create_span.set_attribute(
                    "runtime_task_id", self._id(runtime_task),
                )

            # ---- 5. Submit through TaskManager ----
            async with tracer.span(
                "task_runner.submit_task",
                runtime_task_id=self._id(runtime_task),
                executor_id=target.executor_id,
            ) as submit_span:
                t0 = time.perf_counter()
                result = await self._execute_runtime_task(
                    runtime_task=runtime_task,
                    target=target,
                    context=context,
                )
                submit_span.set_attribute(
                    "duration_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )

            # ---- 6. Publish completion event ----
            await self._publish_completion(
                runtime_task=runtime_task,
                capability=resolution,
                target=target,
                context=context,
                result=result,
            )

            span.set_attribute("status", "completed")

            return {
                "task_id": self._id(runtime_task),
                "task_spec_id": task_id,
                "status": "completed",
                "capability": resolution.capability,
                "plugin": resolution.plugin_name,
                "device_id": target.device_id,
                "executor_id": target.executor_id,
                "result": result,
            }

    # ------------------------------------------------------------------
    # Runtime task creation — uses actual TaskManager API
    # ------------------------------------------------------------------

    async def _create_runtime_task(
        self,
        *,
        task_spec: Any,
        context: Any,
        target: Any,
        capability: Any,
    ) -> Any:

        spec_dict = (
            task_spec.to_dict()
            if hasattr(task_spec, "to_dict")
            else dict(task_spec)
        )

        spec_id = spec_dict.get("id")
        task_type = spec_dict.get("type") or capability.capability
        parameters = spec_dict.get("parameters") or {}
        timeout = spec_dict.get("timeout")
        retry_count = int(spec_dict.get("retry_count", 0) or 0)

        return await self.task_manager.create_initialized(
                input=parameters.get("input") or spec_dict.get("name") or capability.capability,
                task_type=task_type,
                capability=capability.capability,
                device_id=target.device_id,
                executor_id=target.executor_id,
                parameters=parameters,
                user_id=context.user_id,
                session_id=context.session_id,
                timeout=timeout if timeout is not None else 30.0,
                max_retries=retry_count,
                metadata={
                    "plugin": capability.plugin_name,
                    "request_id": context.request_id,
                    "task_spec_id": spec_id,
                    "source_device_id": getattr(context, "source_device_id", None),
                },
            )

    # ------------------------------------------------------------------
    # Runtime task execution — uses actual TaskManager API
    # ------------------------------------------------------------------

    async def _execute_runtime_task(
        self,
        *,
        runtime_task: Any,
        target: Any,
        context: Any,
    ) -> Any:

        runtime_id = self._id(runtime_task)

        return await self.task_manager.submit(runtime_id, wait=True)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def _publish_completion(
        self,
        *,
        runtime_task: Any,
        capability: Any,
        target: Any,
        context: Any,
        result: Any,
    ) -> None:

        if not self.event_bus:
            return

        try:
            await self.event_bus.publish(
                "task.completed",
                {
                    "task_id": self._id(runtime_task),
                    "request_id": context.request_id,
                    "user_id": context.user_id,
                    "device_id": target.device_id,
                    "executor_id": target.executor_id,
                    "capability": capability.capability,
                    "plugin": capability.plugin_name,
                    "result": result,
                },
            )
        except Exception as exc:
            logger.warning("task.completed publish failed: {}", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _id(task: Any) -> str:
        if isinstance(task, dict):
            return str(task.get("id", ""))
        return str(getattr(task, "id", ""))