from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from typing import Any, Callable, Dict, List, Literal, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from pai.event_bus.bus import EventBus
from pai.event_bus.events import EventType
from pai.devices.models import DeviceFrame
from pai.executors.manager import ExecutorManager
from pai.executors.scheduler import ExecutorScheduler
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_resolver import CapabilityResolution, CapabilityResolver
from pai.orchestration.capability_router import CapabilityTarget
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.models import TaskSpec
from pai.common.enums.permission import Origin
from pai.security.authorization.context import ScopeContext
from pai.security.authorization.decision import AuthorizationDecision, DecisionEffect
from pai.security.manager import SecurityManager
from pai.common.task_controls import CancelOutcomeKind, TaskControls
from pai.tasks.lifecycle import InvalidTaskTransition, TaskControlError, TaskLifecycle
from pai.tasks.models import (
    Task,
    TaskPriority,
    TaskResult,
    TaskSource,
    TaskAuthorization,
    TaskRetryPolicy,
    TaskPhase,
    TaskStatus,
    TaskStreamEvent,
    TaskType,
    get_task_timeout,
    utc_now,
)
from pai.tasks.user_errors import error_fields
from pai.tasks.store import TaskStoreProtocol
from pai.tasks.store import TaskStore
from pai.common.manager import Manager

class TaskManager(Manager):
    name = "task_manager"

    def __init__(
        self,
        *,
        store: TaskStoreProtocol,
        scheduler: ExecutorScheduler,
        executor_manager: ExecutorManager,
        capability_resolver: CapabilityResolver,
        device_selector: DeviceSelector,
        security_manager: SecurityManager,
        event_bus: EventBus,
        session_factory: Optional[Callable[[], AsyncSession]],
        retry_policy: Optional[TaskRetryPolicy] = None,
    ) -> None:
        super().__init__(session_factory=session_factory, event_bus=event_bus)
        self.retry_policy = retry_policy or TaskRetryPolicy()
        self._shutting_down = False
        self.cancel_ack_timeout: float = 3.0   # how long a cancel waits for the device to confirm
        self._recovery_tasks: set[asyncio.Task] = set()
        self.store = store
        self.scheduler = scheduler
        self.executor_manager = executor_manager
        self.session_factory = session_factory
        self.capability_resolver = capability_resolver
        self.device_selector = device_selector
        self.authorizer = security_manager
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._paused: set[str] = set()      # ids of tasks the user has paused (deadlines stop counting)
        self._pause_events:  Dict[str, asyncio.Event] = {}   

    async def _on_initialize(self) -> None:
        if self.scheduler is None:
            raise RuntimeError("TaskManager has no ExecutorScheduler")
        
        await self.scheduler.initialize()

        if not self.capability_resolver.is_initialized():
            raise RuntimeError(
                "TaskManager: capability_resolver not initialized"
            )
        if not self.device_selector.is_initialized():
            raise RuntimeError("TaskManager: device_selector not initialized")
        if not self.authorizer.is_initialized():
            raise RuntimeError(
                "TaskManager: authorization_manager not initialized"
            )
        if not self.executor_manager.is_initialized():
            raise RuntimeError(
                "TaskManager: executor_manager not initialized"
            )

        if self._event_bus and not getattr(
            self._event_bus, "is_initialized", lambda: True
        )():
            raise RuntimeError("TaskManager: event_bus not initialized")

        await self.recover_unfinished()

    async def _on_shutdown(self) -> None:
        # Tasks cancelled by shutdown must stay recoverable, not become CANCELLED.
        self._shutting_down = True
        for t in list(self._recovery_tasks):
            t.cancel()
        running = list(self._running_tasks.values())
        for task in running:
            if not task.done():
                task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._running_tasks.clear()


    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            **await super().get_status(user_id=user_id),
            "running_tasks": list(self._running_tasks.keys()),
            "task_count": len(self._running_tasks),
        }

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------

    async def create_task(
        self,
        task_spec: TaskSpec,
        user_id: str,
        session_id: str,
        executor_id: str,
        device_id: str,
        plugin: Optional[str] = None,
        source: TaskSource = TaskSource.USER,
        priority: TaskPriority = TaskPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        parent_task_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        controls: Optional[TaskControls] = None,
    ) -> Task:

        tracer = get_tracer()
        async with tracer.span(
            "task.create",
            task_id=task_spec.id,
            task_type=task_spec.type,
            user_id=user_id,
            session_id=session_id,
            device_id=device_id,
            executor_id=executor_id,
            plugin=plugin
        ) as span:

            task = Task(
                name=task_spec.name,
                type=task_spec.type,
                parameters=task_spec.parameters,
                user_id=user_id,
                session_id=session_id,
                source=source,
                priority=priority,
                timeout=task_spec.timeout,
                max_retries=task_spec.retry_count or self.retry_policy.default_max_retries,
                idempotency_key=idempotency_key,
                metadata=dict(metadata or {}),   # never share one dict between tasks
                parent_task_id=parent_task_id,
                device_id=device_id,
                executor_id=executor_id,
                plugin=plugin,
                requires_confirmation=task_spec.requires_confirmation ,
                confirmation_reason=task_spec.confirmation_reason,
                capability=task_spec.capability,
                spec_id=task_spec.id,
                retryable=task_spec.retryable,
                # From the manifest / built-in definition, never from the plan or the LLM.
                controls=controls or self.controls_for(task_spec.capability),
            )

            await self.store.create(task)
            await self._publish("task.created", task.to_dict())

            span.set_attribute("task_id", task.id)

            logger.info("Task created: {} ({})", task.id, task.type)
            return task


    def controls_for(self, capability: Optional[str], manifest: Any = None) -> TaskControls:
        """What the user may do to a running instance of ``capability``.

        Plugin capability -> its manifest's ``controls``; built-in -> the built-in's own
        declaration; anything unknown -> the safe default (no pause, best-effort cancel).
        """
        if capability:
            if manifest is not None:
                spec = manifest.get_capability(capability)
                if spec is not None:
                    return getattr(spec, "controls", None) or TaskControls()
            builtin = getattr(self.executor_manager, "get", lambda _n: None)("builtin_executor") \
                if self.executor_manager is not None else None
            cap = builtin.registry.get(capability) if builtin is not None and hasattr(builtin, "registry") else None
            if cap is not None:
                return getattr(cap, "controls", None) or TaskControls()
        return TaskControls()

    async def create_initialized(
        self,
        task_spec: TaskSpec,
        context: ExecutionContext,
        target: CapabilityTarget,
        resolution: CapabilityResolution,
        authorization: Any,
        source: TaskSource = TaskSource.USER,
        priority: TaskPriority = TaskPriority.NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Task:  
        # TODO:: Add authorization to the task metadata and/or 
        # Task object so that the executor can use it to authorize the execution of the task.

        tracer = get_tracer()
        async with tracer.span(
            "task.create_initialized",
            task_type=task_spec.type,
            capability=resolution.capability,
            device_id=target.device_id,
            executor_id=target.executor_id,
            user_id=context.user_id,
        ) as span:

            task = await self.create_task(
                task_spec=task_spec,
                user_id=context.user_id,
                session_id=context.session_id,
                source=source,
                priority=priority,
                metadata=metadata,
                device_id=target.device_id,
                executor_id=target.executor_id,
                plugin=task_spec.metadata.get("plugin"),
                parent_task_id=task_spec.metadata.get("parent_task_id"),
                idempotency_key=idempotency_key,
                controls=self.controls_for(resolution.capability, resolution.plugin_manifest),
            )
            span.set_attribute("task_id", task.id)
            return task

    async def find_by_idempotency_key(
        self, key: str, *, user_id: str, session_id: Optional[str],
    ) -> Optional[Task]:
        for task in await self.store.list(user_id=user_id, session_id=session_id):
            if task.idempotency_key == key:
                return task
        return None

    async def join_existing(self, task: Task, wait: bool) -> TaskResult | None:
        if not wait:
            return None
        while not task.terminal:
            live = self._running_tasks.get(task.id)
            if live is not None:
                try:
                    await asyncio.shield(live)
                except (asyncio.CancelledError, Exception):
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise
            else:
                await asyncio.sleep(0.05)      # queued/recovering: wait for its coroutine
            task = await self.store.require(task.id)
        return task.result or TaskResult(
            task_id=task.id, success=task.status == TaskStatus.COMPLETED, error=task.error)

    # ------------------------------------------------------------------
    # Recovery & manual retry
    # ------------------------------------------------------------------

    _UNFINISHED = (
        TaskStatus.CREATED, TaskStatus.INITIALIZED, TaskStatus.QUEUED,
        TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.PAUSED,
    )

    async def recover_unfinished(self) -> list[str]:
        """Re-queue tasks a previous process left unfinished. Returns their ids.

        Terminal tasks are never touched (no double execution). Interrupted work
        keeps its idempotency key, so a device that already finished it can
        de-duplicate the re-send. A task that was PAUSED comes back behind the
        confirmation gate: it waits for the user to resume instead of running.
        """
        recovered: list[str] = []
        for status in self._UNFINISHED:
            for task in await self.store.list(status=status):
                if task.id in self._running_tasks:
                    continue
                if (not task.retryable and task.dispatched_at is not None
                        and status in (TaskStatus.RUNNING, TaskStatus.PAUSED)):
                    # It reached the device and may already have run; never auto re-send a
                    # non-retryable action. (One that never got that far is safe to queue again.)
                    await self._fail_interrupted(task, "Interrupted by a restart (not retryable)")
                    continue
                if status == TaskStatus.PAUSED:
                    task.requires_confirmation = True
                    task.confirmation_reason = "Paused before a restart. Resume to continue."
                if status == TaskStatus.CREATED:
                    TaskLifecycle.initialize(task, capability=task.capability,
                                             device_id=task.device_id, executor_id=task.executor_id)
                elif status != TaskStatus.INITIALIZED:
                    # Was in flight when the process died. (INITIALIZED means it was never
                    # started, or a graceful shutdown already marked it interrupted.)
                    task.mark_interrupted()
                await self.store.save(task)
                await self._publish("task.recovered", task.to_dict())
                recovered.append(task.id)
                self._spawn_recovery(task.id)
        if recovered:
            logger.warning("TaskManager: re-queued {} unfinished task(s): {}", len(recovered), recovered)
        return recovered

    async def _fail_interrupted(self, task: Task, reason: str) -> None:
        task.mark_failed(reason)
        await self.store.save(task)
        await self._publish("task.failed", task.to_dict())

    async def _publish(self, event_type: str, data: Optional[Dict[str, Any]] = None, **kw: Any) -> None:
        """Every ``task.*`` event carries the same plain-language ``error_info`` as the REST views."""
        if isinstance(data, dict) and str(event_type).startswith("task.") and data.get("id") \
                and "error_info" not in data:
            status = str(data.get("status") or "")
            raw = data.get("error") or data.get("last_error")
            if status == "cancelled" and not raw:
                raw = "cancelled"
            if raw or status == "failed":
                data = {**data, "error_info": error_fields(raw, data.get("capability"))}
        await super()._publish(event_type, data, **kw)

    def _spawn_recovery(self, task_id: str) -> None:
        async def _go() -> None:
            try:
                await self.submit(task_id, wait=False)
            except Exception:
                logger.exception("Recovery submit failed for task {}", task_id)
        t = asyncio.create_task(_go(), name=f"recover:{task_id}")
        self._recovery_tasks.add(t)
        t.add_done_callback(self._recovery_tasks.discard)

    async def retry(self, task_id: str) -> Task:
        """User-requested retry of a FAILED task (grants one more attempt)."""
        task = await self.store.require(task_id)
        if task.status != TaskStatus.FAILED:
            raise InvalidTaskTransition(
                f"Task {task_id} cannot be retried from state {task.status.value}"
            )
        task.max_retries = max(task.max_retries, task.retry_count + 1)
        TaskLifecycle.retry(task)
        task.schedule_retry(0.0)            # a user retry starts right away
        await self.store.save(task)
        await self._publish("task.retrying", task.retrying_payload())
        await self.submit(task.id, wait=False)
        return task

    @staticmethod
    def manifest_retryable(resolution: Any, task_spec: TaskSpec) -> bool:
        """The capability's `retryable` flag from its manifest. Without a manifest (built-ins)
        fall back to the spec's value, which the decomposer also read from the manifest."""
        manifest = getattr(resolution, "plugin_manifest", None)
        cap = manifest.get_capability(resolution.capability) if manifest is not None else None
        return bool(cap.retryable) if cap is not None else bool(task_spec.retryable)

    @staticmethod
    def manifest_execution(resolution: Any) -> Dict[str, Any]:
        """`type` / `timeout` overrides for a TaskSpec from the capability's manifest entry
        (empty for built-ins, which declare theirs on the spec the decomposer built)."""
        manifest = getattr(resolution, "plugin_manifest", None)
        cap = manifest.get_capability(resolution.capability) if manifest is not None else None
        if cap is None:
            return {}
        out: Dict[str, Any] = {}
        task_type = getattr(cap, "taskType", None)
        if task_type is not None:
            out["type"] = task_type
        timeout = getattr(cap, "timeoutSeconds", None)
        if timeout:
            out["timeout"] = float(timeout)
        return out

    @staticmethod
    def _deadline(task: Task) -> Optional[float]:
        """Seconds the task may run, or None for no deadline (long-running / stream)."""
        timeout = get_task_timeout(task)
        return None if timeout == float("inf") else timeout

    async def on_stream_frame(self, frame: DeviceFrame) -> None:
        """Publish a device stream.start / stream.frame / stream.end as a realtime event."""
        task = await self.store.get(frame.task_id) if frame.task_id else None
        if task is None:
            logger.debug("stream frame for unknown task {}", frame.task_id)
            return
        kind = frame.type.split(".", 1)[1]
        if kind == "frame" and task.status == TaskStatus.PAUSED:
            return                      # paused: device may still flush a frame; don't surface it
        event = TaskStreamEvent(
            task_id=task.id, user_id=task.user_id, session_id=task.session_id,
            kind=kind, sequence=frame.sequence, data=frame.data,
        )
        await self._publish(EventType.TASK_STREAM, event.model_dump(mode="json"))

    async def apply_authorization(
        self, task: Task, precheck: AuthorizationDecision, context: ExecutionContext,
    ) -> None:
        """Attach the authorization outcome to the task.

        APPROVAL_REQUIRED  -> pause behind the confirmation gate; tokens are
                              minted only after the user approves.
        ALLOW              -> mint tokens bound to this task.
        """
        task.authorization = TaskAuthorization(
            required=True,
            agent_id=context.agent_id,
            origin=context.origin or Origin.AGENT_AUTONOMOUS,
            source_device_id=context.source_device_id,
            scope=ScopeContext.from_execution_context(context).with_device(task.device_id),
            pre_approved=context.pre_approved,
        )
        decision = precheck
        if decision.effect == DecisionEffect.ALLOW:
            # Re-run bound to the *runtime* task id (the pre-check used the spec id).
            decision = await self.authorizer.authorize_task(task)
        if decision.effect == DecisionEffect.DENY:
            raise PermissionError(f"Authorization denied: {decision.reason}")
        if decision.effect == DecisionEffect.APPROVAL_REQUIRED:
            task.authorization.pending = True
            task.requires_confirmation = True
            task.confirmation_reason = (
                task.confirmation_reason or decision.confirmation_reason or decision.reason
            )
            # The store hands back a fresh copy when the task runs (DB-backed), so what was just
            # decided must be persisted, or the confirmation gate never sees it and the action runs.
            await self.store.save(task)
            return
        grant_id = getattr(decision, "consent_grant_id", None)
        if grant_id and hasattr(self.authorizer, "record_grant_use"):
            task.authorization.grant_id = grant_id           # a standing grant approved this run
            await self.authorizer.record_grant_use(grant_id, task)
        elif context.pre_approved and hasattr(self.authorizer, "_cap_spec") and hasattr(
                self.authorizer, "audit_auto_approved"):
            # Trusted pre-approval (admin tester, valid schedule consent) resolved the prompt:
            # leave a trace that the confirmation was skipped, and why.
            _, cap_spec = self.authorizer._cap_spec(task.plugin, task.capability)
            if cap_spec is not None and getattr(cap_spec, "requireConfirmation", False):
                self.authorizer.audit_auto_approved(task, "pre-approved by trusted server code")
        task.authorization.tokens = list(decision.tokens)
        # Capabilities needing no permissions get no token; the decision itself gated them.
        task.authorization.required = bool(decision.tokens)
        await self.store.save(task)

    async def run(
        self,
        task_spec: TaskSpec,
        context: ExecutionContext,
        wait: bool = True,
    ) -> TaskResult | None:
        task_id = task_spec.id
        capability = task_spec.capability
        logger.info(
            "Running planned task {} (capability={})",
            task_id, capability,
        )

        # Idempotency: one (request, plan task) pair maps to exactly one runtime task.
        # A repeated run() (client retry, DAG re-drive, recovery) joins it instead of
        # creating — and executing — a duplicate.
        idempotency_key = f"{context.request_id}:{task_id}"
        existing = await self.find_by_idempotency_key(
            idempotency_key, user_id=context.user_id, session_id=context.session_id)
        if existing is not None:
            logger.info("Task {} already exists for key {} — joining", existing.id, idempotency_key)
            return await self.join_existing(existing, wait)

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
                #  Resolve trusted capability metadata
                resolution = await self.capability_resolver.resolve(
                    task_spec,
                    user_id=context.user_id,
                )
                res_span.set_attribute("plugin", resolution.plugin_id)
                res_span.set_attribute(
                    "target_count", len(resolution.targets),
                )

            span.set_attribute("plugin", resolution.plugin_id)

            # Retry policy comes from the manifest, never from the plan / LLM.
            # Likewise the task type and timeout: whoever built the spec (planner, scheduler,
            # tester, API), the capability's manifest has the last word.
            task_spec = dataclasses.replace(
                task_spec, retryable=self.manifest_retryable(resolution, task_spec),
                **self.manifest_execution(resolution))

            # ---- 2. Select device ----
            # TODO: if the plan specified a device, use that instead of selecting one.
            # Get the preferred device from user preferences that is set in settings page of frontend
            preferred = task_spec.preferred_device

            async with tracer.span(
                "task_runner.select_device",
                capability=resolution.capability,
                preferred_device=preferred,
            ) as sel_span:
                target = await self.device_selector.select(
                    user_id=context.user_id,
                    capability=resolution.capability,
                    task_spec=task_spec,
                    preferred_device=preferred,
                    targets=resolution.targets,
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
                plugin_id=resolution.plugin_id,
            ):

                # # 3. Normalize and validate arguments
                # normalized_args = await self._validate_and_normalize_arguments(
                #     task=task,
                #     capability=resolution,
                # )

                # # 4. Build authorization request from trusted sources
                # auth_request = await self.authorizer.build_request(
                #     user_id=context.user_id,
                #     plugin_id=resolution.plugin_id,
                #     capability_id=resolution.capability,
                #     manifest=resolution.manifest,
                #     arguments=normalized_args,
                #     device_id=target.device_id,
                #     task_id=task_dict.get("id"),
                #     session_id=context.session_id,
                #     agent_id=context.agent_id,
                #     execution_origin=context.execution_origin,
                # )

                # # 5. Evaluate authorization
                # decision = await self.authorizer.evaluate(auth_request)

                # # 6. Resolve approval or deny
                # authorization = await self.authorizer.enforce(
                #     request=auth_request,
                #     decision=decision,
                # )
                            
                authorization = None
                if target.requires_device:
                    # Plugin capability — needs full device+plugin authorization.
                    authorization = await self.authorizer.authorize(
                        user_id=context.user_id,
                        capability=resolution.capability,
                        device_id=target.device_id,
                        plugin_id=resolution.plugin_id,
                        plugin_manifest=resolution.plugin_manifest,
                        parameters=task_spec.parameters,
                        scope=ScopeContext.from_execution_context(context).with_device(target.device_id),
                        task_id=task_spec.id,
                        agent_id=context.agent_id,
                        origin=context.origin or Origin.AGENT_AUTONOMOUS,
                        source_device_id=context.source_device_id,
                        session_id=context.session_id,
                        approved=context.pre_approved,
                    )
                    if authorization.effect == DecisionEffect.DENY:
                        raise PermissionError(f"Authorization denied: {authorization.reason}")
                else:
                    # Built-in — system-provided, no user-installable plugin, no
                    # device. Still log it so the trace shows the skip.
                    logger.debug(
                        "TaskRunner: skipping authorization for built-in %s (user=%s)",
                        resolution.capability, context.user_id,
                    )


            # ---- 4. Create runtime task via TaskManager ----
            async with tracer.span(
                "task_runner.create_task",
                capability=resolution.capability,
                device_id=target.device_id,
            ) as create_span:
                runtime_task = await self.create_initialized(
                    task_spec=task_spec,
                    context=context,
                    target=target,
                    resolution=resolution,
                    authorization=authorization,
                    idempotency_key=idempotency_key,
                    source=(TaskSource.SCHEDULED if context.origin == Origin.SCHEDULED
                            else TaskSource.USER),
                    metadata=dict(task_spec.metadata or {}),   # e.g. schedule_id, for history
                )
                create_span.set_attribute(
                    "runtime_task_id", runtime_task.id,
                )

                if authorization is not None:
                    runtime_task.plugin = runtime_task.plugin or resolution.plugin_id
                    await self.apply_authorization(runtime_task, authorization, context)

            # ---- 5. Submit through TaskManager ----
            async with tracer.span(
                "task_runner.submit_task",
                runtime_task_id=runtime_task.id,
                executor_id=target.executor_id,
            ) as submit_span:
                t0 = time.perf_counter()
                # Execute only through the secure execution gateway
                result = await self.submit(runtime_task.id, wait=wait)
                submit_span.set_attribute(
                    "duration_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )

            # ---- 6. Emit completion event ----
            self._emit(EventType.TASK_DISPATCHED, {
                "task_id": runtime_task.id,
                "task_spec_id": task_id,
                "capability": resolution.capability,
                "plugin_id": resolution.plugin_id,
                "device_id": target.device_id,
                "executor_id": target.executor_id,
                "request_id": context.request_id,
                "user_id": context.user_id,
                "session_id": context.session_id,
            })

            span.set_attribute("status", "completed")

            return result


    async def complete_via_push(
        self,
        task_id: str,
        *,
        payload: Dict[str, Any],
    ) -> Task:
        """Mark a task completed because delivery happened out-of-band
        (push notification, SMS, etc.). Walks the lifecycle explicitly so
        the Task never gets stuck in CREATED."""
        task = await self.store.require(task_id)

        if task.terminal:
            logger.debug(
                "complete_via_push: task %s already %s",
                task_id, task.status.value,
            )
            return task

        TaskLifecycle.queue(task)
        await self.store.save(task)
        await self._publish("task.queued", task.to_dict())

        TaskLifecycle.start(task)
        await self.store.save(task)
        await self._publish("task.started", task.to_dict())

        result = TaskResult(
            success=True,
            task_id=task.id,
            output=payload,
            device_id=task.device_id,
        )
        TaskLifecycle.complete(task, result)
        await self.store.save(task)
        await self._publish("task.completed", task.to_dict())
        return task


    async def fail_undelivered(
        self,
        task_id: str,
        reason: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """Mark a task failed without ever executing it.

        Used when *no* delivery channel is available: push disabled / no
        push token / WS submit rejected / unexpected send error. The task
        still walks the normal lifecycle so state history stays coherent
        and every event a real failure would emit is emitted here too —
        TasksScreen and the admin dashboard see an ordinary failure, just
        with `metadata.undelivered = true`.

        Does NOT retry. Delivery failures are retried by the scheduler's
        `catch_up` policy, not by `TaskManager`. Retrying here would just
        re-hit the same unavailable channel on the next tick and burn
        through the task's retry budget.
        """
        tracer = get_tracer()
        async with tracer.span(
            "task.fail_undelivered",
            task_id=task_id,
            reason=reason,
        ) as span:

            task = await self.store.require(task_id)

            # Terminal or operator-held — respect that, don't override.
            if task.terminal:
                span.set_attribute("skipped", "already_terminal")
                span.set_attribute("status", task.status.value)
                logger.debug(
                    "fail_undelivered: task %s already %s",
                    task_id, task.status.value,
                )
                return task

            if task.status == TaskStatus.PAUSED:
                span.set_attribute("skipped", "paused")
                logger.debug(
                    "fail_undelivered: task %s is paused, leaving as-is",
                    task_id,
                )
                return task

            # ----------------------------------------------------------
            # Walk forward so `fail()` is a legal transition.
            # Each step is guarded by the current status — no-op when
            # the task has already moved past that state.
            # ----------------------------------------------------------

            # CREATED → INITIALIZED
            if task.status == TaskStatus.CREATED:
                TaskLifecycle.initialize(
                    task,
                    capability=task.capability,
                    device_id=task.device_id,
                    executor_id=task.executor_id,
                )
                await self._publish("task.initialized", task.to_dict())

            # INITIALIZED or RETRYING → QUEUED
            # (submit() would do the same; we're mirroring that path.)
            if task.status in {TaskStatus.INITIALIZED, TaskStatus.RETRYING}:
                TaskLifecycle.queue(task)
                await self._publish("task.queued", task.to_dict())

            # QUEUED → RUNNING
            if task.status == TaskStatus.QUEUED:
                TaskLifecycle.start(task)
                await self._publish("task.started", task.to_dict())

            # ----------------------------------------------------------
            # Fail with a structured result.
            # ----------------------------------------------------------
            result = TaskResult(
                success=False,
                task_id=task.id,
                error=reason,
                executor_id=task.executor_id,
                device_id=task.device_id,
                started_at=task.started_at,
                completed_at=task.completed_at,
                metadata={
                    "undelivered": True,
                    "reason": reason,
                    **(extra or {}),
                },
            )

            TaskLifecycle.fail(task, reason, result)
            await self.store.save(task)
            await self._publish("task.failed", task.to_dict())

            span.set_attribute("status", "failed")
            span.set_attribute("final_state", task.status.value)

            logger.warning(
                "Task %s failed (undelivered): %s",
                task_id, reason,
            )
            return task
    
    # ------------------------------------------------------------------
    # Submit / execute
    # ------------------------------------------------------------------

    async def submit(
        self,
        task_id: str,
        *,
        wait: bool = True,
    ) -> TaskResult | None:

        tracer = get_tracer()
        async with tracer.span(
            "task.submit",
            task_id=task_id,
            wait=wait,
        ) as span:

            task = await self.store.require(task_id)

            # create_task() leaves tasks in CREATED; initialize here so every
            # entry path (run(), orchestrator actions, schedules) can submit.
            if task.status == TaskStatus.CREATED:
                TaskLifecycle.initialize(
                    task,
                    capability=task.capability,
                    device_id=task.device_id,
                    executor_id=task.executor_id,
                )
                await self.store.save(task)
                await self._publish("task.initialized", task.to_dict())

            if task.status not in {TaskStatus.INITIALIZED, TaskStatus.RETRYING}:
                raise InvalidTaskTransition(
                    f"Task {task_id} cannot be submitted from "
                    f"state {task.status.value}"
                )

            if not task.executor_id:
                raise ValueError(f"Task {task_id} has no executor_id")

            if self.scheduler is None:
                raise RuntimeError("TaskManager has no ExecutorScheduler")

            TaskLifecycle.queue(task)
            await self.store.save(task)
            await self._publish("task.queued", task.to_dict())

            span.set_attribute("executor_id", task.executor_id)
            span.set_attribute("device_id", task.device_id)

            execution = asyncio.create_task(self._execute(task.id))
            self._running_tasks[task.id] = execution
            execution.add_done_callback(
                lambda _: self._running_tasks.pop(task.id, None)
            )

            if not wait:
                return None

            try:
                return await execution
            except asyncio.CancelledError:
                # The *task* was cancelled (user cancel) vs. the *caller* being
                # cancelled. Only the latter should propagate.
                current = asyncio.current_task()
                if execution.cancelled() and (current is None or current.cancelling() == 0):
                    return TaskResult(task_id=task_id, success=False, error="Task cancelled")
                raise

    async def _confirm_if_needed(self, task: Task, pause_event: asyncio.Event) -> None:
        """Confirmation gate. Called inside _execute between checkpoints.

        If the task requires confirmation, in this order:
          1. server-trusted pre-approval (admin tester, a scheduled action under valid consent)
             -> proceed, audited;
          2. a standing grant (this session / always, parameter-bound) covers exactly this call
             -> proceed, use counted + audited — the SAME ConsentManager.find_grant the
             authorization pipeline uses, so the two can never disagree;
          3. otherwise pause, tell the user what they are approving and which scopes the server
             allows (once / session / always), and block until they resume or cancel.
        Pre-approval and grants never override a DENY: authorization ran before this point.
        """
        if not task.requires_confirmation:
            return

        sec = self.authorizer

        # 1. Trusted pre-approval.
        if task.authorization.pre_approved and hasattr(sec, "audit_auto_approved"):
            sec.audit_auto_approved(task, "pre-approved by trusted server code")
            return

        # 2. A standing grant.
        if task.authorization.grant_id is not None:
            return                              # apply_authorization already counted this run's grant
        if hasattr(sec, "find_grant_for_task"):
            grant = await sec.find_grant_for_task(task)
            if grant is not None:
                task.authorization.grant_id = grant.id
                await sec.record_grant_use(grant.id, task)
                await self.store.save(task)
                return

        # 3. Ask the user.
        if hasattr(sec, "confirmation_info"):
            try:
                task.confirmation = await sec.confirmation_info(task)
            except Exception:
                logger.exception("could not build confirmation info for task {}", task.id)

        # Pause the task — uses existing lifecycle
        TaskLifecycle.pause(task)  # RUNNING → PAUSED
        await self.store.save(task)
        await self._publish("task.awaiting_confirmation", task.to_dict())

        # Clear the checkpoint event; the user's resume() will set it
        pause_event.clear()

        reason = task.confirmation_reason or task.metadata.get("confirmation_reason", "")

        # Emit the confirmation request on the event bus
        # (the notification service subscribes to this and pushes to the device)
        self._emit(EventType.TASK_CONFIRMATION_REQUESTED, {
            "task_id": task.id,
            "user_id": task.user_id,
            "session_id": task.session_id,
            "capability": task.capability,
            "parameters": task.parameters,
            "reason": reason,
            "confirmation": (task.confirmation.model_dump(mode="json")
                             if task.confirmation is not None else None),
        })

        logger.info(
            "task.awaiting_confirmation: id=%s capability=%s user=%s",
            task.id, task.capability, task.user_id,
        )

        # Block until the user resumes or cancels. resume() sets the event;
        # cancel() cancels the asyncio task, which raises CancelledError here.
        await pause_event.wait()

        # If we got here, user resumed. Task status is RUNNING again.
        task.confirmation = None
        await self.store.save(task)
        logger.info("task.confirmation_granted: id=%s", task.id)

    async def _execute(self, task_id: str) -> TaskResult | None:
        tracer = get_tracer()
        # This runs inside the context captured at create_task time,
        # so spans nest under task.submit in the parent trace.
        async with tracer.span(
            "task.execute",
            task_id=task_id,
        ) as span:

            task = await self.store.require(task_id)
            
            # Pause event for this run. Set = running, cleared = paused.
            # Created *before* start() so a pause arriving the instant
            # we go RUNNING has an event to clear.
            pause_event = asyncio.Event()
            pause_event.set()
            self._pause_events[task_id] = pause_event

            span.set_attribute("executor_id", task.executor_id)
            span.set_attribute("device_id", task.device_id)
            span.set_attribute("capability", task.capability)

            try:
                if task.status == TaskStatus.PAUSED:
                    # Paused while still queued: nothing has started — really hold here.
                    pause_event.clear()
                    await pause_event.wait()
                    task = await self.store.require(task_id)
                TaskLifecycle.start(task)
                await self.store.save(task)
                await self._publish("task.started", task.to_dict())

                logger.info(
                    "Executing task {} using {}", task.id, task.executor_id,
                )

                # Checkpoint 1 — pause blocks the tool invocation entirely.
                await pause_event.wait()

                # Confirmation gate — no-op if not needed
                await self._confirm_if_needed(task, pause_event)

                # Mint fresh tokens now (they are short-lived, and the user may have
                # sat on the confirmation prompt). Passing the confirmation gate is
                # the user's approval; without one, approved=False and an unmet
                # APPROVAL_REQUIRED fails closed.
                if task.authorization.required or task.authorization.pending:
                    decision = await self.authorizer.authorize_task(
                        task, approved=task.requires_confirmation,
                    )
                    if decision.effect != DecisionEffect.ALLOW:
                        raise PermissionError(f"Authorization not granted: {decision.reason}")
                    task.authorization.tokens = list(decision.tokens)
                    task.authorization.pending = False
                    task.authorization.required = bool(decision.tokens)

                # Executors only run what carries valid, task-bound tokens.
                if task.authorization.required:
                    self.authorizer.verify_task_tokens(task)

                # Last point before the call reaches the device: a pause taken while we were
                # authorizing / waiting for confirmation really holds here (nothing has run).
                await pause_event.wait()

                # Re-read (a pause/resume/cancel may have saved a newer copy) and mark the
                # call as dispatched. From here on pause/cancel depend on the capability's
                # declared controls and on the device acknowledging.
                task = await self.store.require(task_id)
                task.dispatched_at = utc_now()
                await self.store.save(task)
                await self._publish("task.updated", task.to_dict())

                if task.plugin:
                    self.authorizer.audit_execution_started(
                        user_id=task.user_id, plugin_id=task.plugin, capability=task.capability,
                        device_id=task.device_id, task_id=task.id,
                    )

                async with tracer.span(
                    "task.scheduler_call",
                    task_id=task.id,
                    executor=task.executor_id,
                ) as sched_span:
                    try:
                        if( task.executor_id is None ):
                            raise ValueError("Task has no executor")
                        
                        task_result = await self._run_pausable(
                            task_id,
                            self.scheduler.schedule_task(
                                task=task,
                                executor_id=task.executor_id,
                            ),
                            self._deadline(task),
                        )
                        if task.plugin:
                            if task_result.success:
                                self.authorizer.audit_execution_completed(
                                    user_id=task.user_id, plugin_id=task.plugin,
                                    capability=task.capability, device_id=task.device_id, task_id=task.id)
                            else:
                                self.authorizer.audit_execution_failed(
                                    user_id=task.user_id, plugin_id=task.plugin,
                                    capability=task.capability, device_id=task.device_id, task_id=task.id,
                                    reason=task_result.error or "Execution failed")
                        if task_result.success:
                            TaskLifecycle.complete(task, task_result)
                            await self.store.save(task)
                            await self._publish("task.completed", task.to_dict())
                        else:
                            sched_span.set_attribute("result", task_result.model_dump())
                            # Same path as exceptions/timeouts: retry with backoff if budget remains.
                            return await self._handle_failure(
                                task, task_result.error or "Execution failed", result=task_result,
                                retryable=task_result.retryable)

                        sched_span.set_attribute("result", task_result.model_dump())

                        return task_result

                    except asyncio.TimeoutError:
                        sched_span.set_error(
                            f"timeout after {task.timeout}s"
                        )
                        return await self._handle_failure(
                                task,
                                f"Task timed out after {task.timeout} seconds",
                            )

                    except Exception as exc:
                        sched_span.set_error(f"{type(exc).__name__}: {exc}")
                        raise

            except asyncio.TimeoutError:
                span.set_attribute("status", "timeout")
                return await self._handle_failure(
                    task,
                    f"Task timed out after {task.timeout} seconds",
                )

            except asyncio.CancelledError:
                # cancel()/pause() may have saved a newer copy than our local one: decide on that.
                task = await self.store.get(task_id) or task
                if self._shutting_down and not task.terminal:
                    # Server stopping, not the user cancelling: keep it recoverable...
                    if task.retryable or task.dispatched_at is None:
                        task.mark_interrupted()
                        await self.store.save(task)
                    else:
                        # ...unless it is non-retryable and may already have reached the device:
                        # re-sending could repeat the action, so fail it (manual retry stays possible).
                        await self._fail_interrupted(task, "Interrupted by server shutdown (not retryable)")
                    raise
                if not task.terminal:       # cancel() may already have recorded it
                    TaskLifecycle.cancel(task, "Task execution cancelled")
                    await self.store.save(task)
                    await self._publish("task.cancelled", task.to_dict())
                raise

            except Exception as exc:
                logger.exception("Task execution failed: {}", task.id)
                span.set_error(f"{type(exc).__name__}: {exc}")
                # Authorization failures are decisions, not glitches: never retry them.
                return await self._handle_failure(
                    task, str(exc) or f"{type(exc).__name__} (no details)",
                    retryable=not isinstance(exc, PermissionError))

            finally:
                self._running_tasks.pop(task_id, None)
                self._pause_events.pop(task_id, None)

    async def _run_pausable(self, task_id: str, coro: Any, deadline: Optional[float]) -> Any:
        """Await ``coro`` with a deadline that does NOT run while the user has the task paused.

        (A paused one-shot task must not "time out" because the user took a coffee break.)
        Completion is returned immediately; the 0.25 s slices only meter the deadline.
        """
        run = asyncio.ensure_future(coro)
        remaining = deadline
        last = time.monotonic()
        try:
            while True:
                done, _ = await asyncio.wait({run}, timeout=0.25)
                if run in done:
                    return run.result()
                now = time.monotonic()
                if remaining is not None and task_id not in self._paused:
                    remaining -= now - last
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                last = now
        finally:
            if not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run

    async def _handle_failure(
        self, task: Task, error: str, *, retryable: bool = True,
        result: Optional[TaskResult] = None,
    ) -> TaskResult | None:
        """Fail the task; if budget remains and the error is transient, retry with backoff."""
        tracer = get_tracer()
        will_retry = retryable and task.retryable and task.retry_count < task.max_retries
        async with tracer.span(
            "task.handle_failure",
            task_id=task.id,
            can_retry=will_retry,
        ) as span:

            result = result or TaskResult(
                success=False,
                error=error,
                task_id=task.id,
                executor_id=task.executor_id,
                device_id=task.device_id,
                started_at=task.started_at,
                completed_at=task.completed_at,
            )

            # Move to FAILED (a legal step to RETRYING), but only announce "failed"
            # when we are really giving up — a retried attempt is not a failure to the user.
            if not task.terminal:
                TaskLifecycle.fail(task, error, result)
            await self.store.save(task)

            if not will_retry:
                await self._publish("task.failed", task.to_dict())
                span.set_attribute("action", "give_up")
                return result

            attempt = task.retry_count + 1
            delay = self.retry_policy.delay_for(attempt)
            logger.warning("Retrying task {} ({}/{}) in {:.1f}s: {}",
                           task.id, attempt, task.max_retries, delay, error)
            span.set_attribute("action", "retry")
            # The task is RETRYING for the whole backoff (not FAILED): that is what it is, and it
            # is a phase where pause/cancel are real (nothing is in flight). The user sees it
            # at once: `task.retrying` carries the full task + `retry` (attempt, next_retry_at...).
            TaskLifecycle.retry(task)
            task.last_error = error
            task.schedule_retry(delay)
            await self.store.save(task)
            await self._publish("task.retrying", task.retrying_payload())
            await asyncio.sleep(delay)          # backoff; cancellable (user cancel / shutdown)
            event = self._pause_events.get(task.id)
            if event is not None:
                await event.wait()              # paused during the backoff: hold until resumed
            return await self.submit(task.id, wait=True)

    # ------------------------------------------------------------------
    # Cancellation / pause / resume
    # ------------------------------------------------------------------

    @staticmethod
    def _refuse(decision: Any) -> TaskControlError:
        return TaskControlError(decision.code or "invalid_state", decision.reason or "Not allowed.")

    async def cancel(
        self,
        task_id: str,
        reason: Optional[str] = None,
    ) -> Task:
        """Cancel a task.

        Before the call reaches the device (queued, starting, waiting to retry, waiting for
        confirmation) this really stops it: ``cancel_outcome = not_started``. After dispatch:
        refused (409) if the capability declares ``cancel: none``; otherwise the backend stops
        waiting at once and asks the executor/device to stop, and ``cancel_outcome`` records
        whether the device acknowledged (``acked``) or we gave up waiting (``abandoned`` — the
        device may have completed it).
        """
        tracer = get_tracer()
        async with tracer.span("task.cancel", task_id=task_id, reason=reason):
            task = await self.store.require(task_id)
            if task.status == TaskStatus.CANCELLED:
                return task                     # already cancelled: cancelling again is a no-op, not an error
            decision = task.cancel_decision()
            if not decision.allowed:
                raise self._refuse(decision)

            dispatched = task.phase == TaskPhase.DISPATCHED
            running = self._running_tasks.get(task_id)

            # 1. Ask the executor/device to stop (concurrently: cancelling must not wait for it).
            ack_job: Optional[asyncio.Task] = None
            if dispatched and task.executor_id and self.executor_manager is not None:
                ack_job = asyncio.create_task(self._cancel_on_executor(task, running))

            # 2. Stop waiting right now — this unwinds the wrapper coroutine.
            if running and not running.done():
                running.cancel()
            self._paused.discard(task_id)

            TaskLifecycle.cancel(task, reason or "Cancelled by user")
            task.paused_from = None
            task.cancel_outcome = None if dispatched else CancelOutcomeKind.NOT_STARTED
            await self.store.save(task)
            await self._publish("task.cancelled", task.to_dict())

            # 3. Record what the cancel achieved, then tell clients (task.updated).
            if ack_job is not None:
                try:
                    acked = await asyncio.wait_for(ack_job, timeout=self.cancel_ack_timeout + 1.0)
                except (asyncio.TimeoutError, Exception):
                    acked = False
                task = await self.store.get(task_id) or task
                task.cancel_outcome = CancelOutcomeKind.ACKED if acked else CancelOutcomeKind.ABANDONED
                await self.store.save(task)
                await self._publish("task.updated", task.to_dict())
            return task

    async def _cancel_on_executor(self, task: Task, running: Optional[asyncio.Task]) -> bool:
        """True iff the executor/device confirmed the action stopped."""
        executor = self.executor_manager.get(task.executor_id) if task.executor_id else None
        if executor is not None and getattr(executor, "supports_control_ack", False):
            return bool(await self.executor_manager.cancel_task(task.executor_id, task.id))
        # In-process executors have no ack protocol: stopping IS cancelling the awaiting
        # coroutine. It counts as acked if that coroutine actually unwound in time.
        if executor is not None:
            await self.executor_manager.cancel_task(task.executor_id, task.id)
        if running is None:
            return True
        await asyncio.wait({running}, timeout=self.cancel_ack_timeout)
        return running.done()

    async def pause(self, task_id: str) -> Task:
        """Pause a task — honestly.

        Before dispatch it holds the task at a gate (nothing has run). After dispatch it only
        works if the capability declares ``controls.pause`` AND the device acknowledges; if
        not, HTTP 409 and the task state is NOT changed (no fake "paused").
        """
        tracer = get_tracer()
        async with tracer.span("task.pause", task_id=task_id):
            task = await self.store.require(task_id)
            decision = task.pause_decision()
            if not decision.allowed:
                raise self._refuse(decision)

            if task.phase == TaskPhase.DISPATCHED:
                acked = False
                if task.executor_id and self.executor_manager is not None:
                    acked = bool(await self.executor_manager.pause_task(task.executor_id, task_id))
                if not acked:
                    raise TaskControlError(
                        "control_not_acknowledged",
                        "The device did not confirm the pause, so the task was left running.",
                    )

            event = self._pause_events.get(task_id)
            if event is not None:
                event.clear()                   # the next gate (pre-dispatch / backoff) holds
            self._paused.add(task_id)
            TaskLifecycle.pause(task)           # status -> PAUSED, remembers where to resume
            await self.store.save(task)
            await self._publish("task.paused", task.to_dict())
            return task

    async def resume(self, task_id: str) -> Task:
        tracer = get_tracer()
        async with tracer.span("task.resume", task_id=task_id):
            task = await self.store.require(task_id)
            decision = task.resume_decision()
            if not decision.allowed:
                raise self._refuse(decision)

            if task.phase == TaskPhase.DISPATCHED:
                acked = False
                if task.executor_id and self.executor_manager is not None:
                    acked = bool(await self.executor_manager.resume_task(task.executor_id, task_id))
                if not acked:
                    raise TaskControlError(
                        "control_not_acknowledged",
                        "The device did not confirm the resume; the task is still paused.",
                    )

            was_backoff = task.paused_from == TaskStatus.RETRYING
            TaskLifecycle.resume(task)
            if was_backoff:
                task.schedule_retry(0.0)
            await self.store.save(task)
            self._paused.discard(task_id)
            event = self._pause_events.get(task_id)
            if event is not None:
                event.set()                     # wake the held coroutine
            await self._publish("task.resumed", task.to_dict())
            return task

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get(self, task_id: str) -> Task:
        return await self.store.require(task_id)


    async def list(
        self,
        *,
        status: Optional[TaskStatus] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Task]:
        return await self.store.list(
            status=status,
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )