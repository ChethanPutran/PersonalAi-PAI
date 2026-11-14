"""
Top-level AI task orchestrator.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from loguru import logger

from pai.agents.manager import AgentManager
from pai.llm.models import LLMMessage, LLMRequest
from pai.notifications.push_service import PushService
from pai.planning.models import TaskSpec
from pai.scheduler.scheduler_service import SchedulerService
from pai.storage.repositories.chat import ChatRepository
from pai.tasks.task_tracker import TaskTracker
from pai.devices.manager import DeviceManager
from pai.event_bus.bus import EventBus
from pai.executors.manager import ExecutorManager
from pai.memory.manager import MemoryManager
from pai.observability.tracer import get_tracer
from pai.orchestration.authorization import AuthorizationManager
from pai.orchestration.capability_resolver import CapabilityResolver
from pai.orchestration.capability_router import CapabilityRouter
from pai.orchestration.device_selector import DeviceSelector
from pai.orchestration.execution_context import (
    ContextManager,
    ExecutionContext,
)
from pai.orchestration.commands import (
    CommandRegistry,
    CommandResult,
    default_command_registry,
)
from pai.orchestration.task_runner import TaskRunner
from pai.planning.dag_scheduler import DAGScheduler
from pai.planning.planner import Planner
from pai.plugins.manager import PluginManager
from pai.security.manager import SecurityManager
from pai.tasks.manager import TaskManager
from pai.common.manager import Manager
import json
from pydantic import BaseModel, Field
from pai.planning.goal_decomposer import CHAT_CAPABILITY_ID


import re

# at module level
_RETRY_PATTERNS = [
    re.compile(r"\bretry\b", re.I),
    re.compile(r"\btry\s+again\b", re.I),
    re.compile(r"\bagain\b", re.I),
    re.compile(r"\bredo\b", re.I),
    re.compile(r"\brepeat\b", re.I),
    re.compile(r"\bdo\s+(?:it|that)\s+again\b", re.I),
    re.compile(r"\bcontinue\b", re.I),
    re.compile(r"\bsame\b", re.I),
    re.compile(r"\bwhy\s+did\s+(?:it|that|the\s+last\s+\w+)\s+fail\b", re.I),
    re.compile(r"\bwhat\s+happened\b", re.I),
    re.compile(r"\b(?:didn'?t|did\s+not)\s+work\b", re.I),
]


def _looks_like_retry(text: str) -> bool:
    return any(p.search(text) for p in _RETRY_PATTERNS)

class OrchestrationError(RuntimeError):
    pass

class OrchestratorConfig(BaseModel):
    """Orchestrator configuration."""
    goal_tokens_trace_size: int = Field(default=200)
    memory_recall_limit: int = Field(default=5)
    memory_block_max_size: int = Field(default=800)
    tool_summary_block_max_size: int = Field(default=800)
    prompt_trace_max_size: int = Field(default=800)
    chat_reply_max_tokens: int = Field(default=800)
    chat_reply_temperature: float = Field(default=0.7)

class TaskOrchestrator(Manager):
    name="TaskOrchestrator"
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
        capability_resolver: CapabilityResolver,
        task_runner: TaskRunner,
        capability_router: CapabilityRouter,
        authorization_manager: AuthorizationManager,
        device_selector: DeviceSelector,
        event_bus: EventBus,
        memory_manager: MemoryManager,
        dag_scheduler: DAGScheduler,
        scheduler_service: SchedulerService,
        chat_repo: ChatRepository,
        push_service: PushService,
        command_registry: Optional[CommandRegistry] = None,   
        orchestrator_config: OrchestratorConfig = OrchestratorConfig(),
    ):
        super().__init__(event_bus=event_bus)
        self.context_manager = context_manager
        self.planner = planning_engine
        self.task_manager = task_manager
        self.agent_manager = agent_manager
        self.device_manager = device_manager
        self.plugin_manager = plugin_manager
        self.security_manager = security_manager
        self.executor_manager = executor_manager
        self.memory_manager = memory_manager
        self.capability_router = capability_router
        self.capability_resolver = capability_resolver
        self.authorization_manager = authorization_manager
        self.device_selector = device_selector
        self.task_runner = task_runner
        self.dag_scheduler = dag_scheduler
        self.chat_repo = chat_repo
        self.push_service = push_service
        self.command_registry = (                               
            command_registry or default_command_registry()
        )
        self._tracker = TaskTracker()
        self._initialized = False
        self.goal_tokens_trace_size = orchestrator_config.goal_tokens_trace_size
        self.memory_recall_limit = orchestrator_config.memory_recall_limit
        self.memory_block_max_size = orchestrator_config.memory_block_max_size
        self.tool_summary_block_max_size = orchestrator_config.tool_summary_block_max_size
        self.prompt_trace_max_size = orchestrator_config.prompt_trace_max_size
        self.chat_reply_max_tokens = orchestrator_config.chat_reply_max_tokens
        self.chat_reply_temperature = orchestrator_config.chat_reply_temperature
        self.scheduler_service = scheduler_service
    
    async def initialize(self) -> None:
        # ── 3. Sanity: every persisting manager has a session_factory ──
        # Ordered by dependency — insertion order matters.
        # Anything later in this list may assume earlier items are ready.
        ordered = [
            ("device_manager",        self.device_manager),
            ("plugin_manager",        self.plugin_manager),
            ("security_manager",      self.security_manager),
            ("task_manager",          self.task_manager),
            ("executor_manager",      self.executor_manager),
            ("agent_manager",         self.agent_manager),
            ("memory_manager",        self.memory_manager),
            ("authorization_manager", self.authorization_manager),
            ("capability_router",     self.capability_router),
            ("capability_resolver",   self.capability_resolver),
            ("task_runner",           self.task_runner),
            ("dag_scheduler",         self.dag_scheduler),
        ]

        initialized: list[str] = []
        for name, mgr in ordered:
            if mgr is None:
                logger.debug("Skipping {} (not wired)", name)
                continue

            logger.info("Initializing {}...", name)
            try:
                init = getattr(mgr, "initialize", None)
                if init is None:
                    logger.debug(
                        "{} has no initialize() — treating as ready", name,
                    )
                else:
                    await init()
                initialized.append(name)
            except Exception as exc:
                logger.exception(
                    "Failed to initialize {} ({} initialized so far: {})",
                    name, len(initialized), initialized,
                )
                # Roll back partial initialization in reverse order.
                await self._shutdown_managers(
                    [n for n in reversed(initialized)],
                )
                raise

        self._initialized = True
        logger.info(
            "TaskOrchestrator initialized ({} components ready)",
            len(initialized),
        )

    # =============================================================
    # SLASH COMMANDS
    # =============================================================

    async def _handle_command(
        self,
        *,
        raw: str,
        user_id: str,
        session_id: Optional[str],
        source_device_id: Optional[str],
    ) -> Dict[str, Any]:

        resolved = self.command_registry.resolve(raw)

        # `/xyz` with an unknown name → helpful error, no context needed
        if resolved is None:
            name = raw.split()[0] if raw.split() else "/"
            msg = f"Unknown command {name}. Type /help for the list."
            logger.info(
                "Unknown command from user={}: {}", user_id, name,
            )
            return self._command_response(
                status="completed",
                command_name="unknown",
                message=msg,
                session_id=session_id
            )

        cmd, args = resolved

        execution_context = await self.context_manager.create_context(
            user_id=user_id,
            session_id=session_id,
            source_device_id=source_device_id,
            goal=raw,
        )

        tracer = get_tracer()
        with tracer.start_trace(
            "orchestrator.command",
            trace_id=execution_context.request_id,
            attributes={
                "user_id": user_id,
                "command": cmd.name,
                "args": args,
            },
        ) as trace_session:
            
            async with tracer.span(
                            "orchestrator.command.execute",
                            user_id=user_id,
                        ) as dev_span:
                
                try:
                    
                    result: CommandResult = await cmd.execute(
                        args,
                        context=execution_context,
                        orchestrator=self,
                    )
                    logger.info(f"Command result : {result}")
                    dev_span.set_attribute("result", result)
                except Exception as exc:
                    logger.exception(
                        "Command /{} failed for user={}", cmd.name, user_id,
                    )
                    trace_session.set_status("failed")
                    return self._command_response(
                        status="failed",
                        command_name=cmd.name,
                        session_id=execution_context.session_id,
                        message=f"/{cmd.name} failed: {exc}",
                        request_id=execution_context.request_id,
                    )

            trace_session.set_status(result.status)
            logger.info(
                "Command /{} completed for user={}", cmd.name, user_id,
            )
            return self._command_response(
                status=result.status,
                command_name=cmd.name,
                message=result.message,
                data=result.data,
                session_id=execution_context.session_id,
                request_id=execution_context.request_id,
            )

    @staticmethod
    def _command_response(
        *,
        status: str,
        command_name: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
         session_id: Optional[str] = None,
        request_id: str = "",
    ) -> Dict[str, Any]:
        """Shape the response identically to a normal run() result so the
        HTTP route and the frontend don't need to special-case commands."""
        task_id = f"command:{command_name}"
        return {
            "request_id": request_id,
            "status": status,
            "session_id": session_id,
            "results": [
                {
                    "task_id": task_id,
                    "status": status,
                    "capability": f"command.{command_name}",
                    "reply": message,
                    "data": data,
                }
            ],
            "completed": [task_id] if status == "completed" else [],
            "failed": [task_id] if status != "completed" else [],
            "message": message,          # convenience for the HTTP route
        }
    
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
            raise ValueError("Goal cannot be empty")

    
        # ---- Built-in slash command? Bypass the whole pipeline. ---- NEW
        if self.command_registry.is_command(goal):
            return await self._handle_command(
                raw=goal.strip(),
                user_id=user_id,
                session_id=session_id,
                source_device_id=source_device_id,
            )

        # ---- Create execution context ----
        execution_context = await self.context_manager.create_context(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            source_device_id=source_device_id,
            goal=goal,
            metadata=context or {},
        )

        logger.info(
            "Orchestrating request={} user={} goal={!r}",
            execution_context.request_id,
            user_id,
            goal,
        )

        tracer = get_tracer()
        t_start = time.perf_counter()

        with tracer.start_trace(
            "orchestrator.run",
            trace_id=execution_context.request_id,
            attributes={
                "user_id": user_id,
                "session_id": execution_context.session_id,
                "source_device_id": source_device_id,
                "goal": goal[:500],
            },
        ) as trace_session:

            # ── Device snapshot at request start ──
            # Records the exact set of devices visible to this user *before*
            # any planning happens. If a request fails with "no capabilities",
            # the diff between this snapshot and what the router later reports
            # tells you whether devices were lost mid-request.
            async with tracer.span(
                "orchestrator.device_snapshot",
                user_id=user_id,
            ) as dev_span:
                try:
                    devices = await self.device_manager.list_devices(user_id)
                    dev_span.set_attribute("count", len(devices))
                    dev_span.set_attribute(
                        "devices",
                        [
                            {
                                "id":       d.id,
                                "name":     getattr(d, "name", "") or "",
                                "type":     getattr(d, "device_type", "unknown"),
                                "status":   (
                                    d.status.value
                                    if hasattr(d.status, "value")
                                    else str(d.status)
                                ),
                                "user_id":  getattr(d, "user_id", None),
                                "connected": d.id in self.device_manager._connections,
                            }
                            for d in devices
                        ],
                    )
                except Exception as exc:
                    dev_span.set_error(str(exc))
                    logger.warning("Device snapshot failed: {}", exc)

            # ---- Publish goal event ----
            async with tracer.span("orchestrator.publish_goal"):
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
                self._emit("chat.status",
                    {
                    "session_id":execution_context.session_id,
                    "request_id":execution_context.request_id,
                    "stage":"planning",
                    "message":"Understanding your request...",
                    } )

            # ---- Build planning context ----
            planning_context = execution_context.to_dict()
            if context:
                planning_context.update(context)
            planning_context["user_id"] = user_id
            planning_context["source_device_id"] = source_device_id

            # ---- Planner (span covers full planner work) ----
            async with tracer.span(
                "orchestrator.planner",
                goal=goal[:200],
            ) as planner_span:
                plan = await self.planner.create_plan(
                    goal,
                    planning_context,
                )
                plan_id = plan.id
                task_specs = plan.tasks

                planner_span.set_attribute("plan_id", plan_id)
                planner_span.set_attribute("task_count", len(task_specs))

            logger.info(
                "Created plan={} with {} tasks",
                plan_id, len(task_specs),
            )
            self._emit( "chat.status", {
                "session_id":execution_context.session_id,
                "request_id":execution_context.request_id,
                "stage":"planned",
                "message":f"Plan ready: {len(task_specs)} step(s)",
                "task_count":len(task_specs),
                "task_capabilities":[
                    getattr(t, "capability", "?") for t in task_specs
                ],
                }
            )


            if not task_specs:
                trace_session.set_status("failed")
                raise OrchestrationError("Planner returned an empty plan")

            # ---- Publish plan.created ----
            async with tracer.span("orchestrator.publish_plan_created"):
                await self._publish("plan.created", {
                    "request_id": execution_context.request_id,
                    "session_id": execution_context.session_id,     
                    "plan_id": plan_id,
                    "user_id": user_id,
                    "goal": goal,
                    "task_count": len(task_specs),
                })

            # ---- Execute plan ----
            async with tracer.span(
                "orchestrator.execute_plan",
                plan_id=plan_id,
                task_count=len(task_specs),
            ) as exec_span:
                result = await self._execute_plan(
                    plan=plan,
                    task_specs=task_specs,
                    context=execution_context,
                )
                exec_span.set_attribute(
                    "status", result.get("status", "unknown"),
                )
                exec_span.set_attribute(
                    "completed", len(result.get("completed", [])),
                )
                exec_span.set_attribute(
                    "failed", len(result.get("failed", [])),
                )

            # ---- Persist conversation ----
            async with tracer.span("orchestrator.persist_interaction"):
                await self.context_manager.record_interaction(
                    user_id=user_id,
                    user_message=goal,
                    response=result,
                    session_id=execution_context.session_id,
                )

   
            # ---- Memory (fire-and-forget, tracked) ----
            success = result.get("status") == "completed"

            # Snapshot capabilities at write time so the memory entry
            # records what was true *when it was recorded*. Combined
            # with `success`, this lets recall filter stale entries
            # after capabilities change.
            capabilities: Optional[set[str]] = None
            if self.capability_router is not None:
                try:
                    capabilities = set(
                        (await self.capability_router.list_capabilities(
                            user_id,
                        )) or []
                    )
                except Exception as exc:
                    logger.debug(
                        "Could not snapshot capabilities for memory: {}",
                        exc,
                    )

            self._tracker.spawn(
                self._store_interaction(
                    goal=goal,
                    result=result,
                    user_id=user_id,
                    session_id=execution_context.session_id,
                    request_id=execution_context.request_id,
                    success=success,
                    capabilities=capabilities,
                ),
                name=f"store_interaction:{execution_context.request_id}",
            )

            # ---- Final status ----
            elapsed_ms = (time.perf_counter() - t_start) * 1000
            logger.info(
                "orchestrator.run complete: status={} duration={:.1f}ms",
                result.get("status", "unknown"), elapsed_ms,
            )
            trace_session.set_status(result.get("status", "completed"))

            self._emit("chat.status",{
                "session_id":execution_context.session_id,
                "request_id":execution_context.request_id,
                "stage":result.get("status", "done"),
                "message":"Done" if result.get("status") == "completed" else "Failed"}
            )
            
            return result

    # =============================================================
    # PLAN EXECUTION
    # =============================================================

    async def _execute_plan(
        self,
        *,
        plan: Any,
        task_specs: list[TaskSpec],
        context: ExecutionContext,
    ) -> Dict[str, Any]:

        tracer = get_tracer()

        # Separate chat vs tool tasks up front
        chat_tasks = [t for t in task_specs if self._is_chat_task(t)]
        tool_tasks = [t for t in task_specs if not self._is_chat_task(t)]

        logger.info(
            "Executing plan={} ({} tool task(s), {} chat task(s))",
            self._get_id(plan), len(tool_tasks), len(chat_tasks),
        )

        all_results: list = []
        all_completed: list = []
        all_failed: list = []

        # ---------- Phase 1: tool tasks ----------
        if tool_tasks:
            self._emit("chat.status",{
                "session_id":context.session_id,
                "request_id":context.request_id,
                "stage":"executing",
                "message":f"Running {len(tool_tasks)} tool task(s)...",
                "tool_count":len(tool_tasks),
            }
            )
            async with tracer.span(
                "orchestrator.execute_tools",
                plan_id=self._get_id(plan),
                task_count=len(tool_tasks),
            ) as tools_span:
                if self.dag_scheduler is None:
                    logger.warning(
                        "DAG Scheduler unavailable; running tool tasks sequentially"
                    )
                    tool_result = await self._execute_sequential(tool_tasks, context)
                else:
                    try:
                        tool_result = await self.dag_scheduler.execute(
                            tool_tasks,
                            context=context,
                        )
                    except Exception as exc:
                        logger.exception("DAG execution failed: {}", exc)
                        await self._publish("plan.failed", {
                            "request_id": context.request_id,
                            "session_id": context.session_id,               
                            "plan_id": self._get_id(plan),
                            "status": "failed",
                            "error": str(exc),
                        })
                        raise OrchestrationError(
                            f"DAG execution failed: {exc}"
                        ) from exc

                all_results.extend(tool_result.get("results", []))
                all_completed.extend(tool_result.get("completed", []))
                all_failed.extend(tool_result.get("failed", []))
                tools_span.set_attribute("completed", len(all_completed))
                tools_span.set_attribute("failed", len(all_failed))

        # ---------- Phase 2: chat task (always runs last) ----------
        for chat_task in chat_tasks:
            self._emit("chat.status",{
                "session_id":context.session_id,
                "request_id":context.request_id,
                "stage":"replying",
                "message":"Composing reply...",
            }
            )
            chat_id = self._get_id(chat_task)
            try:
                reply = await self._chat_reply(
                    goal=plan.goal if hasattr(plan, "goal") else "",
                    task=chat_task,
                    context=context,
                    prior_results=all_results,
                )
                all_results.append({
                    "task_id": chat_id,
                    "status": "completed",
                    "capability": CHAT_CAPABILITY_ID,
                    "reply": reply,
                })
                all_completed.append(chat_id)
            except Exception as exc:
                logger.exception("Chat reply failed: {}", exc)
                all_failed.append(chat_id)
                all_results.append({
                    "task_id": chat_id,
                    "status": "failed",
                    "capability": CHAT_CAPABILITY_ID,
                    "error": str(exc),
                })

        # ---------- Phase 3: publish result ----------
        status = "failed" if all_failed else "completed"
        result = {
            "request_id": context.request_id,
            "plan_id": self._get_id(plan),
            "status": status,
            "results": all_results,
            "session_id": context.session_id, 
            "completed": all_completed,
            "failed": all_failed,
        }

        await self._publish(
            "plan.completed" if status == "completed" else "plan.failed",
            result,
        )

        return result

        # =============================================================
    # CHAT HANDLING
    # =============================================================

    @classmethod
    def _is_chat_task(cls, task: Any) -> bool:
        """True iff the task is a conversational reply request."""
        data = cls._to_dict(task) if hasattr(cls, "_to_dict") else (
            task if isinstance(task, dict)
            else task.to_dict() if hasattr(task, "to_dict")
            else getattr(task, "__dict__", {})
        )
        cap = data.get("capability") or data.get("type")
        return cap == CHAT_CAPABILITY_ID

    async def _chat_reply(
        self,
        *,
        goal: str,
        task: Any,
        context: ExecutionContext,
        prior_results: list,
    ) -> str:
        """Generate a natural-language reply via the LLM.

        Injects:
        • Live capabilities   — ground truth about what's installable
        • Chat history        — recent turns in this session
        • Memory              — past sessions; on retry, this session's
                                failed turn is included too (with its error)

        The three sources are complementary, not redundant: history gives
        conversational continuity (even on success), memory gives long-term
        recall, and the retry path threads the needle by surfacing the
        detailed failure reason that only memory has (``raw_result``).
        """
        tracer = get_tracer()

        async with tracer.span(
            "orchestrator.chat_reply",
            goal=goal[:self.goal_tokens_trace_size],
            prior_result_count=len(prior_results),
        ) as span:

            task_dict = self._to_dict(task)
            params = task_dict.get("parameters") or {}
            tone = params.get("tone", "friendly")

            # Retry intent: when the user says "try again" / "what happened"
            # / "why did it fail", we surface the previous attempt from
            # *this* session in the memory block. Otherwise it stays excluded.
            is_retry = _looks_like_retry(goal or "")
            span.set_attribute("is_retry", is_retry)

            # ── 1. Live capabilities ──
            capability_block = ""
            current_capabilities: set[str] = set()
            if self.capability_router is not None:
                try:
                    caps_by_id = (
                        await self.capability_router
                        .describe_capabilities_with_schema(context.user_id)
                    )
                    current_capabilities = set(caps_by_id.keys())
                    if caps_by_id:
                        lines = [
                            "YOUR LIVE CAPABILITIES — verified, currently "
                            "installed and enabled for this user. You CAN "
                            "use them on the user's devices:",
                        ]
                        for cap_id in sorted(caps_by_id):
                            info = caps_by_id[cap_id] or {}
                            desc = (info.get("description") or "").strip()
                            lines.append(
                                f"- {cap_id}: {desc}" if desc else f"- {cap_id}"
                            )
                        capability_block = "\n".join(lines)
                        span.set_attribute("capability_count", len(caps_by_id))
                        span.set_attribute(
                            "capabilities", sorted(caps_by_id.keys()),
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch capabilities for chat prompt: {}", exc,
                    )

            # ── 2. Chat history (recent turns in this session) ──
            history_block = ""
            if self.chat_repo is not None and context.session_id:
                try:
                    msgs = await self.chat_repo.recent_messages(
                        context.session_id,
                        user_id=context.user_id,
                        limit=10,
                    )
                    if msgs:
                        history_block = "\n".join(
                            f"{getattr(m, 'role', 'unknown')}: "
                            f"{str(getattr(m, 'content', '') or '')[:300]}"
                            for m in msgs
                        )
                        span.set_attribute("history_message_count", len(msgs))
                except Exception as exc:
                    logger.warning("Failed to fetch chat history: {}", exc)
                    span.set_error(f"history_fetch_failed: {exc}")

            # ── 3. Memories ──
            #
            # exclude_session=False on retry: the *current* session's failed
            # turn lives in memory with `raw_result` metadata that carries the
            # actual error (e.g. "camera device busy"). Excluding the session
            # would drop that, leaving the LLM with only the sanitized chat
            # text ("I'm sorry, I couldn't take the photo") and no reason.
            #
            # include_failed follows the same switch — auto-derived inside
            # recall_for_prompt, passed explicitly here for clarity.
            memory_block = ""
            if self.memory_manager is not None:
                memory_block = await self.memory_manager.recall_for_prompt(
                    goal,
                    user_id=context.user_id,
                    limit=self.memory_recall_limit,
                    session_id=context.session_id,
                    exclude_session=not is_retry,
                    current_capabilities=current_capabilities,
                    include_failed=is_retry,
                )
                memory_block = memory_block[:self.memory_block_max_size]
                span.set_attribute("memory_injected", bool(memory_block))

            # ── 4. Compose the system prompt ──
            parts = [
                "You are a warm, friendly personal AI assistant running "
                "on the user's devices.",
                "Respond in plain, natural language as if talking to a "
                "friend.",
                "Do not include internal reasoning, step-by-step thinking, "
                "constraints analysis, or meta-commentary about your task.",
                "Do not prefix your reply with anything. Just answer.",

                # NEW: the model was imitating tool-call patterns it saw in
                # history. Make it explicit that tool calls are not its job.
                "IMPORTANT: Do NOT emit tool-call syntax or JSON such as "
                '{"tool": "...", "args": {...}} or {"capability": "..."}. '
                "Tools have already been executed by the system before this "
                "message; your job is only to write the natural-language "
                "reply. If a past message in this conversation contains "
                "tool-call syntax, it is a bug in the system — ignore it and "
                "respond in plain language only.",
            ]

            if capability_block:
                parts.append(capability_block)
                parts.append(
                    "IMPORTANT: When the user asks what you can do, "
                    "answer from the LIVE CAPABILITIES above. Do NOT "
                    "claim you lack a camera, body, or device access — "
                    "those capabilities are real and you invoke them "
                    "on the user's behalf."
                )

            if memory_block:
                if capability_block:
                    parts.append(
                        "PAST INTERACTIONS (historical context only). "
                        "Some of these predate the current capabilities "
                        "and may contain outdated statements such as "
                        "'I cannot take a photo'. Trust the LIVE "
                        "CAPABILITIES section above and the current "
                        "user message over those past statements:\n\n"
                        + memory_block
                    )
                else:
                    parts.append(memory_block)

            # History goes closest to the user turn — the LLM should weight
            # recent conversation more heavily than older memory.
            if history_block:
                parts.append(
                    "RECENT CONVERSATION IN THIS SESSION "
                    "(oldest first, most recent last):\n" + history_block
                )

            system_prompt = "\n\n".join(parts)

            # ── 5. Compose the user turn ──
            if prior_results:
                tool_summary = json.dumps(
                    prior_results, indent=2, default=str,
                )[:self.tool_summary_block_max_size]
                user_msg = (
                    f"User message: {goal}\n\n"
                    f"Results from the tools that were executed:\n"
                    f"{tool_summary}\n\n"
                    f"Now reply to the user in natural language, "
                    f"summarizing what was done and any relevant data."
                )
            else:
                user_msg = goal

            prompt = f"{system_prompt}\n\n{user_msg}"

            span.set_attribute("prompt", prompt[:self.prompt_trace_max_size])

            try:
                # Use `generate()` instead of `complete()` here because
                # we want `finish_reason` and `usage` in the trace.
                # `complete()` returns only the text, hiding truncation.
                llm_response = await self.planner.llm.generate_request(LLMRequest(
                    messages=[LLMMessage(role="user", content=prompt)],
                    temperature=self.chat_reply_temperature,
                    max_tokens=self.chat_reply_max_tokens,
                ))
            except Exception as exc:
                span.set_error(f"llm_failed: {exc}")
                raise

            reply = llm_response.content or ""
            finish_reason = llm_response.finish_reason


            span.set_attribute("reply", reply)
            span.set_attribute("reply_length", len(reply))
            span.set_attribute("tone", tone)
            span.set_attribute("finish_reason", finish_reason)
            if llm_response.usage is not None:
                span.set_attribute("prompt_tokens", llm_response.usage.prompt_tokens)
                span.set_attribute("completion_tokens", llm_response.usage.completion_tokens)
                span.set_attribute("total_tokens", llm_response.usage.total_tokens)

            if finish_reason == "length":
                # Truncated by max_tokens — the reply the user sees is
                # cut off mid-sentence. Log loudly; the caller may want
                # to retry with a higher limit or surface a warning.
                logger.warning(
                    "chat reply truncated at max_tokens={} "
                    "(request={}); consider raising chat_reply_max_tokens",
                    self.chat_reply_max_tokens,
                    context.request_id,
                )

            logger.info(
                "Chat reply generated: request={} length={} finish_reason={}",
                context.request_id, len(reply), finish_reason,
            )
            return reply
         
    async def _execute_sequential(
        self,
        task_specs: list[Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:

        tracer = get_tracer()

        async with tracer.span(
            "orchestrator.execute_sequential",
            task_count=len(task_specs),
        ) as seq_span:

            results = []
            completed = []
            failed = []

            remaining = {
                self._get_id(task): task
                for task in task_specs
            }

            while remaining:
                progress = False

                for task_id, task in list(remaining.items()):
                    task_dict = self._to_dict(task)
                    dependencies = task_dict.get("depends_on", []) or []

                    if not all(dependency in completed for dependency in dependencies):
                        continue

                    progress = True
                    capability = task_dict.get("capability") or task_dict.get("type")

                    async with tracer.span(
                        f"seq.task:{capability}",
                        task_id=task_id,
                        capability=capability,
                    ) as task_span:
                        try:
                            result = await self.task_runner.run(
                                task, context=context,
                            )
                            results.append(result)
                            completed.append(task_id)
                            task_span.set_attribute("status", "completed")
                        except Exception as exc:
                            logger.exception("Task {} failed", task_id)
                            failed.append(task_id)
                            task_span.set_error(f"{type(exc).__name__}: {exc}")
                            results.append({
                                "task_id": task_id,
                                "status": "failed",
                                "error": str(exc),
                            })

                    del remaining[task_id]

                if not progress:
                    seq_span.set_error("deadlock")
                    raise OrchestrationError(
                        "Unable to make progress executing task DAG"
                    )

            status = "failed" if failed else "completed"
            seq_span.set_attribute("status", status)

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

        params = params or {}

        context = await self.context_manager.create_context(
            user_id=user_id,
            session_id=session_id,
            source_device_id=source_device_id,
            goal=capability,
        )

        tracer = get_tracer()

        with tracer.start_trace(
            "orchestrator.execute_action",
            trace_id=context.request_id,
            attributes={
                "user_id": user_id,
                "capability": capability,
                "device_id": device_id,
                "source_device_id": source_device_id,
            },
        ) as trace_session:

            # ---- Resolve plugin ----
            async with tracer.span(
                "action.resolve_capability",
                capability=capability,
            ) as res_span:
                plugin_resolution = await self.capability_resolver.resolve(
                    {
                        "type": capability,
                        "capability": capability,
                        "parameters": params,
                    },
                    user_id=user_id,
                )
                res_span.set_attribute(
                    "plugin", plugin_resolution.plugin_name,
                )

            # ---- Select device ----
            async with tracer.span(
                "action.select_device",
                capability=capability,
                preferred_device=device_id,
            ) as sel_span:
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
                sel_span.set_attribute("device_id", target.device_id)
                sel_span.set_attribute("executor_id", target.executor_id)

            # ---- Authorize ----
            async with tracer.span(
                "action.authorize",
                capability=capability,
                device_id=target.device_id,
            ):
                await self.authorization_manager.authorize(
                    user_id=user_id,
                    capability=capability,
                    device_id=target.device_id,
                    plugin_name=plugin_resolution.plugin_name,
                    task={
                        "type": capability,
                        "parameters": params,
                    },
                )

            # ---- Create + submit task ----
            async with tracer.span(
                "action.create_task",
                capability=capability,
            ):
                runtime_task = await self.task_manager.create_initialized(
                    input="Task input for capability execution",
                    task_type=capability,
                    capability=capability,
                    device_id=target.device_id,
                    executor_id=target.executor_id,
                    parameters=params,
                    user_id=user_id,
                    session_id=context.session_id,
                    timeout=context.timeout,
                    max_retries=context.max_retries,
                    metadata={
                        "plugin": plugin_resolution.plugin_name,
                        "request_id": context.request_id,
                        "session_id": context.session_id,
                        "user_id": context.user_id,
                        "source_device_id": context.source_device_id,
                    },
                )

            async with tracer.span(
                "action.submit_task",
                task_id=runtime_task.id,
            ) as submit_span:
                result = await self.task_manager.submit(
                    runtime_task.id,
                    wait=True,
                )
                submit_span.set_attribute(
                    "result_status",
                    result.get("status") if isinstance(result, dict) else "unknown",
                )

            trace_session.set_status("completed")

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
    user_id: str,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
     success: bool = True,
        capabilities: Optional[set[str]] = None,
) -> None:
        if self.memory_manager is None:
            raise RuntimeError("Memory manager is not configured")

        tracer = get_tracer()
        async with tracer.span(
            "orchestrator.store_interaction",
            user_id=user_id,
            success=success,
        ):
            try:
                await self.memory_manager.store_interaction(
                        goal,
                        result,
                        user_id=user_id,
                        session_id=session_id,
                        request_id=request_id,
                        capabilities=capabilities,
                    )
            except Exception as exc:
                logger.warning("Failed to store interaction: {}", exc)


    async def sweep_stale_capability_memories(
        self,
        user_id: Optional[str] = None,
    ) -> int:
        """
        Remove memories that made negative claims about capabilities
        that are now available.

        Called after `CapabilityRouter.invalidate()` — i.e. any time
        a plugin is enabled/disabled, installed, or a device comes
        online. The read-time filter in `_chat_reply` already handles
        correctness; this is housekeeping so the vector store doesn't
        accumulate dead entries over time.

        Returns the total number of stale documents deleted.
        """
        if self.memory_manager is None or self.capability_router is None:
            return 0

        sweep = getattr(
            self.memory_manager, "sweep_stale_capability_memories", None,
        )
        if sweep is None:
            return 0

        # Resolve which users to sweep.
        target_user = user_id or self._default_sweep_user_id()
        if target_user is None:
            logger.debug("sweep_stale: no user_id and no default — skipping")
            return 0

        try:
            capabilities = set(
                await self.capability_router.list_capabilities(target_user)
            )
        except Exception as exc:
            logger.warning(
                "sweep_stale: could not list capabilities for {}: {}",
                target_user, exc,
            )
            return 0

        if not capabilities:
            return 0

        total = 0
        for cap_id in capabilities:
            try:
                removed = await sweep(cap_id, user_id=target_user)
                total += removed or 0
            except Exception as exc:
                logger.debug(
                    "sweep_stale for {} failed: {}", cap_id, exc,
                )

        if total:
            logger.info(
                "sweep_stale: removed {} stale memory doc(s) for user={} "
                "(now-available capabilities={})",
                total, target_user, sorted(capabilities),
            )
        return total

    def _default_sweep_user_id(self) -> Optional[str]:
        """
        When `invalidate()` was called without a user_id, there is no
        single user to sweep. Sweeping every user in the cache is the
        most thorough option, but `CapabilityRouter` doesn't expose the
        cache keys — and sweeping every user on every global invalidation
        (which fires on every plugin install across all users) would be
        wasteful.

        Practical answer: skip. The read-time filter covers those cases;
        the sweep catches the common case where a user enabled their own
        plugin.
        """
        return None
    

    async def get_status(self, *, user_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "user_id": user_id,
            "planner": await self.planner.get_status(user_id=user_id),
            "task_manager": await self.task_manager.get_status(user_id=user_id),
            "device_manager": await self.device_manager.get_status(user_id=user_id),
            "plugin_manager": await self.plugin_manager.get_status(user_id=user_id),
            "executor_manager": await self.executor_manager.get_status(user_id=user_id),
            "capability_resolver": await self.capability_resolver.get_status(user_id=user_id),
            "authorization_manager": await self.authorization_manager.get_status(user_id=user_id),
            "device_selector": await self.device_selector.get_status(user_id=user_id),
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
        raise TypeError(f"Cannot convert {type(value)} to dict")

    @classmethod
    def _get_id(cls, value: Any) -> str:
        data = cls._to_dict(value)
        return str(data.get("id", getattr(value, "id", "")))

    async def shutdown(self) -> None:
        if not self._initialized:
            return
        self._initialized = False


        # 1. Drain tracked background tasks first — they may write to
        #    memory, which we're about to close.
        await self._tracker.cancel_all(timeout=5.0)

        # 2. Shut down components in reverse initialization order.
        #    Dependents go before dependencies so nothing is still
        #    reading a closed backend.
        ordered = [
            "dag_scheduler",
            "task_runner",
            "capability_resolver",
            "capability_router",
            "authorization_manager",
            "memory_manager",
            "agent_manager",
            "executor_manager",
            "task_manager",
            "security_manager",
            "plugin_manager",
            "device_manager",
            "event_bus", 
        ]
        await self._shutdown_managers(ordered)

        logger.info("TaskOrchestrator shutdown complete")

    async def _shutdown_managers(self, names: list[str]) -> None:
        """Best-effort shutdown of named components. Never raises."""
        for name in names:
            mgr = getattr(self, name, None)
            if mgr is None:
                continue
            try:
                if name == "executor_manager":
                    await mgr.shutdown_all()
                else:
                    shutdown = getattr(mgr, "shutdown", None)
                    if shutdown is None:
                        continue
                    result = shutdown()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:
                logger.warning(
                    "Failed to shut down {}: {}: {}",
                    name, type(exc).__name__, exc,
                )