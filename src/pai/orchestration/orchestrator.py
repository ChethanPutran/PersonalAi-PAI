"""
Top-level AI task orchestrator.

This is the central application execution layer.

Responsibilities:

    User Goal
        ↓
    Context
        ↓
    Planner
        ↓
    Plan
        ↓
    Plan Verification
        ↓
    DAG Scheduler
        ↓
    TaskRunner
        ↓
    Authorization
        ↓
    Capability Resolution
        ↓
    Device Selection
        ↓
    TaskManager
        ↓
    ExecutorManager
        ↓
    Device
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from loguru import logger

from pai.agents.manager import AgentManager
from pai.devices.manager import DeviceManager
from pai.devices.manager import DeviceManager
from pai.executors.manager import ExecutorManager
from pai.orchestration.authorization import (
    AuthorizationManager,
)
from pai.orchestration.capability_resolver import (
    CapabilityResolver,
)
from pai.orchestration.device_selector import (
    DeviceSelector,
)
from pai.orchestration.execution_context import (
    ContextManager,
    ExecutionContext,
)
from pai.orchestration.task_runner import (
    TaskRunner,
)
from pai.planning.planner import Planner
from pai.plugins.manager import PluginManager
from pai.security.manager import SecurityManager
from pai.tasks.manager import TaskManager


class OrchestrationError(RuntimeError):
    pass


class TaskOrchestrator:

    def __init__(
        self,
        *,
        context_manager: ContextManager,
        planning_engine: Planner,
        task_manager: TaskManager,
        device_manager: DeviceManager,
        plugin_manager: PluginManager,
        security_manager: SecurityManager,
        executor_manager: ExecutorManager,
        agent_manager: AgentManager,
        executor_scheduler: Any,
        capability_router: Any,
        event_bus: Any,
        memory_manager: Any,
        dag_scheduler: Any,
    ):

        self.context_manager = context_manager
        self.planner = planning_engine

        self.task_manager = task_manager
        self.agent_manager = agent_manager

        self.device_manager = device_manager
        self.plugin_manager = plugin_manager
        self.security_manager = security_manager

        self.executor_manager = (
            executor_manager
            or executor_scheduler
        )

        self.event_bus = event_bus
        self.memory_manager = memory_manager

        self.capability_router = capability_router

        # ---------------------------------------------------------
        # Orchestration services
        # ---------------------------------------------------------

        self.capability_resolver = CapabilityResolver(
            plugin_manager=plugin_manager,
            capability_router=capability_router,
        )

        self.authorizer = AuthorizationManager(
            security_manager=security_manager,
            plugin_manager=plugin_manager,
            device_manager=device_manager,
        )

        self.device_selector = DeviceSelector(
            device_manager=device_manager,
            executor_manager=self.executor_manager,
        )

        self.task_runner = TaskRunner(
            task_manager=task_manager,
            capability_resolver=self.capability_resolver,
            device_selector=self.device_selector,
            authorization_manager=self.authorizer,
            executor_manager=self.executor_manager,
            event_bus=event_bus,
        )

        self.dag_scheduler = dag_scheduler

        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True

        logger.info(
            "TaskOrchestrator initialized"
        )

    # =============================================================
    # PUBLIC API
    # =============================================================

    async def run(
        self,
        *,
        user_id: str,
        goal: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        source_device_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        if not self._initialized:
            await self.initialize()

        if not goal or not goal.strip():
            raise ValueError(
                "Goal cannot be empty"
            )

        # ---------------------------------------------------------
        # 1. Create request context
        # ---------------------------------------------------------

        execution_context = (
            await self.context_manager.create_context(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                source_device_id=source_device_id,
                goal=goal,
                metadata=context or {},
            )
        )

        logger.info(
            "Orchestrating request={} user={} goal={!r}",
            execution_context.request_id,
            user_id,
            goal,
        )

        # ---------------------------------------------------------
        # 2. Publish goal event
        # ---------------------------------------------------------

        await self._publish(
            "agent.goal",
            {
                "request_id": execution_context.request_id,
                "user_id": user_id,
                "session_id": execution_context.session_id,
                "goal": goal,
                "source_device_id": source_device_id,
            },
        )

        # ---------------------------------------------------------
        # 3. Add goal to memory/context
        # ---------------------------------------------------------

        planning_context = execution_context.to_dict()

        if context:
            planning_context.update(context)

        # ---------------------------------------------------------
        # 4. Ask Planning module to create Plan
        # ---------------------------------------------------------

        plan = await self.planner.create_plan(
            goal,
            planning_context,
        )

        plan_dict = self._to_dict(plan)

        plan_id = plan_dict.get("id")

        logger.info(
            "Created plan={} with {} tasks",
            plan_id,
            len(plan_dict.get("tasks", [])),
        )

        # ---------------------------------------------------------
        # 5. Convert Plan into TaskSpecs
        # ---------------------------------------------------------

        task_specs = list(
            plan_dict.get("tasks", [])
        )

        if not task_specs:
            raise OrchestrationError(
                "Planner returned an empty plan"
            )

        # ---------------------------------------------------------
        # 6. Execute DAG
        # ---------------------------------------------------------

        await self._publish(
            "plan.created",
            {
                "request_id": execution_context.request_id,
                "plan_id": plan_id,
                "user_id": user_id,
                "goal": goal,
                "task_count": len(task_specs),
            },
        )

        result = await self._execute_plan(
            plan=plan,
            task_specs=task_specs,
            context=execution_context,
        )

        # ---------------------------------------------------------
        # 7. Persist conversation
        # ---------------------------------------------------------

        await self.context_manager.record_interaction(
            user_id=user_id,
            user_message=goal,
            response=result,
        )

        # ---------------------------------------------------------
        # 8. Memory
        # ---------------------------------------------------------

        asyncio.create_task(
            self._store_interaction(
                goal=goal,
                result=result,
            )
        )

        return result

    # =============================================================
    # PLAN EXECUTION
    # =============================================================

    async def _execute_plan(
        self,
        *,
        plan: Any,
        task_specs: list[Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:

        if self.dag_scheduler is None:
            return await self._execute_sequential(
                task_specs,
                context,
            )

        # DAGScheduler accepts a task_runner callable.
        runner = lambda task: self.task_runner.run(
            task,
            context=context,
        )

        try:
            dag_result = await self.dag_scheduler.execute(
                task_specs,
                task_runner=runner,
            )
        except TypeError:
            # Supports the constructor-injected runner design
            # from the planning module.
            dag_result = await self.dag_scheduler.execute(
                task_specs
            )

        status = (
            dag_result.get(
                "status",
                "completed",
            )
        )

        result = {
            "request_id": context.request_id,
            "plan_id": self._get_id(plan),
            "status": status,
            "results": dag_result.get(
                "results",
                [],
            ),
            "completed": dag_result.get(
                "completed",
                [],
            ),
            "failed": dag_result.get(
                "failed",
                [],
            ),
        }

        await self._publish(
            "plan.completed"
            if status == "completed"
            else "plan.failed",
            result,
        )

        return result

    async def _execute_sequential(
        self,
        task_specs: list[Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:

        results = []
        completed = []
        failed = []

        # Simple dependency-aware execution fallback.
        remaining = {
            self._get_id(task): task
            for task in task_specs
        }

        while remaining:

            progress = False

            for task_id, task in list(
                remaining.items()
            ):

                task_dict = self._to_dict(task)

                dependencies = task_dict.get(
                    "depends_on",
                    [],
                ) or []

                if not all(
                    dependency in completed
                    for dependency in dependencies
                ):
                    continue

                progress = True

                try:
                    result = await self.task_runner.run(
                        task,
                        context=context,
                    )

                    results.append(result)
                    completed.append(task_id)

                except Exception as exc:

                    logger.exception(
                        "Task {} failed",
                        task_id,
                    )

                    failed.append(task_id)

                    results.append({
                        "task_id": task_id,
                        "status": "failed",
                        "error": str(exc),
                    })

                del remaining[task_id]

            if not progress:
                raise OrchestrationError(
                    "Unable to make progress executing task DAG"
                )

        status = (
            "failed"
            if failed
            else "completed"
        )

        return {
            "request_id": context.request_id,
            "status": status,
            "results": results,
            "completed": completed,
            "failed": failed,
        }

    # =============================================================
    # DIRECT CAPABILITY API
    # =============================================================

    async def execute_action(
        self,
        *,
        user_id: str,
        capability: str,
        params: Optional[Dict[str, Any]] = None,
        device_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source_device_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a single capability without planning.

        Useful for:
            - mobile UI actions
            - API calls
            - system generated actions
            - plugin callbacks
        """

        params = params or {}

        context = await self.context_manager.create_context(
            user_id=user_id,
            session_id=session_id,
            source_device_id=source_device_id,
            goal=capability,
        )

        # ---------------------------------------------------------
        # Capability
        # ---------------------------------------------------------

        plugin_resolution = (
            await self.capability_resolver.resolve({
                "type": capability,
                "capability": capability,
                "parameters": params,
            })
        )

        # ---------------------------------------------------------
        # Device
        # ---------------------------------------------------------

        target = await self.device_selector.select(
            user_id=user_id,
            capability=capability,
            task={
                "type": capability,
                "capability": capability,
                "parameters": params,
                "device_id": device_id,
            },
            preferred_device=device_id,
        )

        # ---------------------------------------------------------
        # Authorization
        # ---------------------------------------------------------

        await self.authorizer.authorize(
            user_id=user_id,
            capability=capability,
            device_id=target.device_id,
            plugin_name=plugin_resolution.plugin_name,
            task={
                "type": capability,
                "parameters": params,
            },
        )

        # ---------------------------------------------------------
        # Runtime task
        # ---------------------------------------------------------

        runtime_task = (
            await self.task_manager.create(
                title=capability,
                input=params,
                user_id=user_id,
                session_id=context.session_id,
                task_type=capability,
                parameters=params,
                device_id=target.device_id,
                executor_id=target.executor_id,
                plugin=plugin_resolution.plugin_name,
            )
        )

        result = await self.task_manager.execute(
            runtime_task,
            context=context,
        )

        return {
            "status": "completed",
            "task_id": self._get_id(runtime_task),
            "capability": capability,
            "plugin": plugin_resolution.plugin_name,
            "device_id": target.device_id,
            "executor_id": target.executor_id,
            "result": result,
        }

    # =============================================================
    # MEMORY
    # =============================================================

    async def _store_interaction(
        self,
        *,
        goal: str,
        result: Dict[str, Any],
    ) -> None:

        if self.memory_manager is None:
            return

        try:
            method = getattr(
                self.memory_manager,
                "store_interaction",
                None,
            )

            if method:
                await method(
                    goal,
                    result,
                )

        except Exception as exc:
            logger.warning(
                "Failed to store interaction: {}",
                exc,
            )

    # =============================================================
    # EVENTS
    # =============================================================

    async def _publish(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:

        if self.event_bus is None:
            return

        try:
            await self.event_bus.publish(
                event_type,
                data,
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish {}: {}",
                event_type,
                exc,
            )

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "planner": self.planner.get_status(),
            "task_manager": self.task_manager.get_status(),
            "device_manager": self.device_manager.get_status(),
            "plugin_manager": self.plugin_manager.get_status(),
            "executor_manager": self.executor_manager.get_status(),
            "capability_resolver": self.capability_resolver.get_status(),
            "authorizer": self.authorizer.get_status(),
            "device_selector": self.device_selector.get_status(),
        }

    # =============================================================
    # HELPERS
    # =============================================================

    @staticmethod
    def _to_dict(value: Any) -> Dict[str, Any]:

        if isinstance(value, dict):
            return value

        if hasattr(value, "to_dict"):
            return value.to_dict()

        if hasattr(value, "__dict__"):
            return dict(value.__dict__)

        raise TypeError(
            f"Cannot convert {type(value)} to dict"
        )

    @classmethod
    def _get_id(cls, value: Any) -> str:
        data = cls._to_dict(value)

        return str(
            data.get(
                "id",
                getattr(value, "id", ""),
            )
        )

    async def shutdown(self) -> None:
        self._initialized = False

        logger.info(
            "TaskOrchestrator shutdown complete"
        )