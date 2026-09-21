"""
Task runner used by the planning DAG scheduler.

DAGScheduler gives us a TaskSpec.
TaskRunner converts that declarative TaskSpec into an executable
runtime task through TaskManager.
"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger


class TaskRunner:

    def __init__(
        self,
        *,
        task_manager: Any,
        capability_resolver: Any,
        device_selector: Any,
        authorization_manager: Any,
        executor_manager: Any = None,
        event_bus: Any = None,
    ):
        self.task_manager = task_manager
        self.capability_resolver = capability_resolver
        self.device_selector = device_selector
        self.authorizer = authorization_manager
        self.executor_manager = executor_manager
        self.event_bus = event_bus

    async def __call__(self, task_spec: Any, *, context: Any) -> Dict[str, Any]:
        return await self.run(
            task_spec=task_spec,
            context=context,
        )
    async def run(
        self,
        task_spec: Any,
        *,
        context: Any,
    ) -> Dict[str, Any]:

        task_dict = (
            task_spec.to_dict()
            if hasattr(task_spec, "to_dict")
            else dict(task_spec)
        )

        task_id = task_dict.get("id")

        logger.info(
            "Running planned task {}",
            task_id,
        )

        # ---------------------------------------------------------
        # 1. Resolve capability/plugin
        # ---------------------------------------------------------

        capability = await self.capability_resolver.resolve(
            task_spec
        )

        # ---------------------------------------------------------
        # 2. Select device
        # ---------------------------------------------------------

        target = await self.device_selector.select(
            user_id=context.user_id,
            capability=capability.capability,
            task=task_spec,
            preferred_device=task_dict.get(
                "preferred_device"
            ),
        )

        # ---------------------------------------------------------
        # 3. Authorization
        # ---------------------------------------------------------

        await self.authorizer.authorize(
            user_id=context.user_id,
            capability=capability.capability,
            device_id=target.device_id,
            plugin_name=capability.plugin_name,
            task=task_dict,
        )

        # ---------------------------------------------------------
        # 4. Create runtime Task
        # ---------------------------------------------------------

        runtime_task = await self._create_runtime_task(
            task_spec=task_spec,
            context=context,
            target=target,
            capability=capability,
        )

        # ---------------------------------------------------------
        # 5. Execute through TaskManager
        # ---------------------------------------------------------

        result = await self._execute_runtime_task(
            runtime_task=runtime_task,
            target=target,
            context=context,
        )

        # ---------------------------------------------------------
        # 6. Event
        # ---------------------------------------------------------

        if self.event_bus:
            await self.event_bus.publish(
                "task.completed",
                {
                    "task_id": self._id(runtime_task),
                    "request_id": context.request_id,
                    "user_id": context.user_id,
                    "device_id": target.device_id,
                    "executor_id": target.executor_id,
                    "capability": capability.capability,
                    "result": result,
                },
            )

        return {
            "task_id": self._id(runtime_task),
            "status": "completed",
            "capability": capability.capability,
            "plugin": capability.plugin_name,
            "device_id": target.device_id,
            "executor_id": target.executor_id,
            "result": result,
        }

    async def _create_runtime_task(
        self,
        *,
        task_spec: Any,
        context: Any,
        target: Any,
        capability: Any,
    ) -> Any:

        method = getattr(
            self.task_manager,
            "create_from_spec",
            None,
        )

        if method:
            return await method(
                task_spec=task_spec,
                user_id=context.user_id,
                session_id=context.session_id,
                device_id=target.device_id,
                executor_id=target.executor_id,
                plugin=capability.plugin_name,
            )

        # Alternative API.
        method = getattr(
            self.task_manager,
            "create_task",
            None,
        )

        if method:
            task_dict = (
                task_spec.to_dict()
                if hasattr(task_spec, "to_dict")
                else dict(task_spec)
            )

            return await method(
                user_id=context.user_id,
                session_id=context.session_id,
                task_type=task_dict.get("type"),
                parameters=task_dict.get(
                    "parameters",
                    {},
                ),
                device_id=target.device_id,
                executor_id=target.executor_id,
                plugin=capability.plugin_name,
            )

        raise RuntimeError(
            "TaskManager does not expose create_from_spec() "
            "or create_task()"
        )

    async def _execute_runtime_task(
        self,
        *,
        runtime_task: Any,
        target: Any,
        context: Any,
    ) -> Any:

        method = getattr(
            self.task_manager,
            "execute",
            None,
        )

        if method:
            return await method(
                runtime_task,
                context=context,
            )

        method = getattr(
            self.task_manager,
            "execute_task",
            None,
        )

        if method:
            return await method(
                runtime_task,
                context=context,
            )

        # Last-resort executor-manager integration.
        if self.executor_manager:
            task_dict = (
                runtime_task.to_dict()
                if hasattr(runtime_task, "to_dict")
                else dict(runtime_task)
            )

            return await self.executor_manager.execute(
                task=task_dict,
                device=target.device,
                executor_id=target.executor_id,
            )

        raise RuntimeError(
            "No task execution interface available"
        )

    @staticmethod
    def _id(task: Any) -> str:
        return str(
            getattr(
                task,
                "id",
                task.get("id") if isinstance(task, dict) else "",
            )
        )