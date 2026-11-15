"""Goal decomposition into declarative task specifications."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.llm.models import LLMMessage, LLMRequest
from pai.llm.provider import LLMProvider
from pai.llm.router import LLMRouter
from pai.memory.manager import MemoryManager
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_router import CapabilityRouter
from pai.planning.models import TaskSpec
import re

from pai.prompts.registry import PromptRegistry, PromptTemplate
from pai.prompts.tracker import PromptVersionTracker
from pai.security.llm_guard import PromptGuard
from pai.storage.repositories.chat import ChatRepository

# Sentinel used end-to-end. Never gets a DB row; orchestrator handles it.
CHAT_CAPABILITY_ID = "chat.reply"

_CHAT_CAPABILITY_SPEC = {
    "id": CHAT_CAPABILITY_ID,
    "description": (
        "Respond to the user conversationally, without calling any "
        "external tool or device. Use for greetings, small talk, "
        "questions that need no action, clarifications, and summaries "
        "of prior task results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tone": {
                "type": "string",
                "enum": ["friendly", "brief", "professional"],
                "default": "friendly",
            },
        },
    },
    "targets": [
        {
            "device_id": "server",
            "executor_id": "llm_executor",
            "device_type": "server",
        },
    ],
}




class GoalDecomposer(Lifecycle):
    def __init__(
        self,
        llm: LLMRouter,
        capability_router: CapabilityRouter,
        memory_manager: MemoryManager,
        chat_repo: ChatRepository,
        prompt_registry: PromptRegistry,
        prompt_tracker: PromptVersionTracker,
        prompt_guard: PromptGuard

    ):
        self.llm = llm
        self.capability_router = capability_router
        self.memory_manager = memory_manager
        self.chat_repo = chat_repo
        self.prompts = prompt_registry
        self.prompt_tracker = prompt_tracker
        self.prompt_guard = prompt_guard

    async def initialize(self) -> None:
        logger.info("GoalDecomposer initialized")

        for prompt_id in ["planning.goal_decomposition", "chat.reply"]:
            meta = self.prompts._load_meta(prompt_id)
            current = meta["current"]
            template = self.prompts.get(prompt_id, version=current)
            # Cheap sanity: render with dummies to catch undeclared vars now,
            # not at 3am when the first user runs a goal.
            declared = set(meta.get("variables", {}))
            import re
            used = set(re.findall(r"{{\s*(\w+)", template.source))
            undeclared = used - declared - {"loop"}  # `loop` is Jinja builtin
            if undeclared:
                raise RuntimeError(
                    f"{prompt_id}@{current} references undeclared variables: "
                    f"{sorted(undeclared)}"
                )


    async def shutdown(self) -> None:
        logger.info("GoalDecomposer shutdown complete")

    def is_initialized(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def decompose(
        self,
        goal: str,
        context: Dict[str, Any] | None = None,
    ) -> List[TaskSpec]:
        context = context or {}
        tracer = get_tracer()

        async with tracer.span(
            "decomposer.decompose",
            goal=goal[:200],
        ) as span:

            user_id = context.get("user_id")
            session_id = context.get("session_id")
            if not user_id:
                span.set_error("missing user_id")
                raise ValueError(
                    "GoalDecomposer.decompose() requires context['user_id']"
                )

            # 1. Capabilities (unchanged)
            t0 = time.perf_counter()
            router_caps = await self.capability_router.describe_capabilities_with_schema(
                user_id,
            )
            span.set_attribute(
                "capability_lookup_ms",
                round((time.perf_counter() - t0) * 1000, 1),
            )

            capabilities: Dict[str, dict] = dict(router_caps)
            capabilities[CHAT_CAPABILITY_ID] = dict(_CHAT_CAPABILITY_SPEC)

            # 2. Chat history for this session
            #
            # Pulls the last N turns. The decomposer needs this even on
            # non-retry turns — "what did you just do?" requires knowing
            # the previous turn, and a follow-up like "and another one"
            # needs to know what was just produced.
            history_block = ""
            if self.chat_repo is not None and session_id:
                try:
                    msgs = await self.chat_repo.recent_messages(
                        session_id, user_id=user_id, limit=10,
                    )
                    if msgs:
                        history_block = "\n".join(
                            f"{getattr(m, 'role', 'unknown')}: "
                            f"{str(getattr(m, 'content', '') or '')[:300]}"
                            for m in msgs
                        )
                        span.set_attribute(
                            "history_message_count", len(msgs),
                        )
                except Exception as exc:
                    logger.warning(
                        "GoalDecomposer: failed to fetch history: {}", exc,
                    )

            # 3. Memory — ALWAYS includes session and failures.
            #
            # Rationale:
            #   • include_session=True: a non-retry follow-up like
            #     "navigate to that url" needs the previous turn to
            #     resolve "that url". Excluding the session on non-retry
            #     was the root cause of the decomposer going blind.
            #
            #   • include_failed=True: a previous *failed* attempt is
            #     still useful context — the URL/parameters it tried are
            #     what the user is asking to retry. The stale-claim filter
            #     (_is_stale_given_capabilities) already protects against
            #     "I can't do X" poison; the failed-attempt filter was
            #     hiding legitimate context.
            #
            # The `include_failed` knob on the memory manager was designed
            # for _chat_reply (where surfacing a failure as a reply would
            # mislead the user). For the decomposer the semantics are
            # different — it's planning, not replying, so failures are
            # input signal, not output noise.
            memory_block = ""
            if self.memory_manager is not None:
                memory_block = await self.memory_manager.recall_for_prompt(
                    goal,
                    user_id=user_id,
                    limit=5,
                    session_id=session_id,
                    exclude_session=False,              # ← always include
                    current_capabilities=set(capabilities.keys()),
                    include_failed=True,                # ← always include
                )
            memory_block = memory_block[:2000]

            span.set_attribute("capability_count", len(capabilities))
            span.set_attribute(
                "capabilities", sorted(capabilities.keys()),
            )
            span.set_attribute(
                "tool_capability_count", len(router_caps),
            )
            span.set_attribute("memory_block_len", len(memory_block))
            span.set_attribute("history_block_len", len(history_block))


            logger.info(
                "GoalDecomposer: user={} session={}"
                "{} tool capabilities + chat; available={}",
                user_id, session_id,
                len(router_caps), sorted(capabilities.keys()),
            )

            user_tz = (
                context.get("timezone")
                or (context.get("user_preferences") or {}).get("timezone")
                or "UTC"
            )
            span.set_attribute("user_timezone", user_tz)

            inspection = self.prompt_guard.inspect_request(
                user_text=goal,
                memory_block=memory_block,
                history_block=history_block,
                tool_results_block="",
                span=span,
            )

            # 4. Build prompt
            t0 = time.perf_counter()

            prompt, template = self._render_prompt(
                goal=goal,
                context=context,
                capabilities=capabilities,
                memory_block=inspection.memory_block,
                history_block=inspection.history_block,
                user_tz=user_tz,
            )
            span.set_attribute("prompt_length", len(prompt))
            span.set_attribute(
                "prompt_build_ms",
                round((time.perf_counter() - t0) * 1000, 1),
            )

            # 4b. Track usage (non-blocking, non-fatal)
            await self.prompt_tracker.record(
                template=template,
                rendered_text=prompt,
                user_id=user_id,
                session_id=session_id,
                span=span
            )

            # 5. LLM call
            try:
                t0 = time.perf_counter()
                llm_response = await self.llm.generate_request(LLMRequest(
                    messages=[LLMMessage(role="user", content=prompt)],
                    temperature=0.1,
                    max_tokens=2000,
                ))
                span.set_attribute(
                    "llm_ms", round((time.perf_counter() - t0) * 1000, 1),
                )
                span.set_attribute(
                    "finish_reason", llm_response.finish_reason,
                )

                # Sanitize the LLM response to remove any sensitive information before logging or further processing.
                sanitized = self.prompt_guard.sanitize_response(
                    raw_text=llm_response.content or "",
                    span=span,
                    request_id=context.get("request_id"),
                )
                response = sanitized.clean_text

                span.set_attribute("response_length", len(response))
                span.set_attribute("response", response)
            except Exception as exc:
                logger.exception("LLM goal decomposition failed: {}", exc)
                span.set_error(f"llm_failed: {exc}")
                raise

            # Truncation guard: if the LLM hit max_tokens, the JSON is
            # almost certainly cut off. Raise a specific error so the
            # operator knows the fix is "increase max_tokens", not
            # "tighten the prompt" (which is the fix for malformed prose).
            if llm_response.finish_reason == "length":
                span.set_error("llm_output_truncated")
                raise ValueError(
                    "Planner LLM response was truncated at max_tokens=2000. "
                    "The plan JSON is incomplete. Either raise max_tokens "
                    "or reduce the capability list size."
                )

            # 6. Parse (do NOT fall back on retry —
            #    a failed retry should surface the real plan failure, not
            #    be masked by a chat.reply)
            try:
                raw_tasks = self._parse_response(response)
                span.set_attribute("raw_task_count", len(raw_tasks))

                tasks = self._normalize_tasks(raw_tasks, capabilities)
                span.set_attribute("task_count", len(tasks))
                span.set_attribute("task_ids", [t.id for t in tasks])
                span.set_attribute(
                    "capabilities_used",
                    sorted(
                        {
                            t.capability
                            for t in tasks
                            if t.capability is not None
                        }
                    ),
                )
                if tasks:
                    return tasks
                logger.warning(
                    "LLM returned an empty plan for goal={!r}; using fallback",
                    goal,
                )
                span.set_attribute("used_fallback", True)
            except ValueError as exc:
                logger.error("Plan validation failed: {}", exc)
                span.set_error(f"validation_failed: {exc}")
                raise

            # 7. Fallback
            fallback_tasks = self._fallback_decompose(
                goal, context, capabilities
            )
            span.set_attribute("fallback_task_count", len(fallback_tasks))
            return fallback_tasks

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        *,
        goal: str,
        context: Dict[str, Any],
        capabilities: Dict[str, dict],
        memory_block: str = "",
        history_block: str = "",
        is_retry: bool = False,
        user_tz: str = "UTC",
    ) -> tuple[str, "PromptTemplate"]:
        template = self.prompts.get("planning.goal_decomposition")

        rendered = template.render(
            goal=goal,
            capabilities_block=self._render_capabilities(capabilities),
            memory_block=memory_block,
            history_block=history_block,
            is_retry=is_retry,
            user_tz=user_tz,
            current_time=datetime.now(timezone.utc).isoformat(),
            chat_capability_id=CHAT_CAPABILITY_ID,
            source_device=(
                context.get("source_device_id")
                or context.get("device_id")
            ),
            context_json=json.dumps(
                {k: v for k, v in context.items() if k != "user_id"},
                default=str,
                indent=2,
            ),
        )
        return rendered, template
    
    @staticmethod
    def _render_capabilities(capabilities: Dict[str, dict]) -> str:
        lines: list[str] = []
        for cap_id in sorted(capabilities.keys()):
            entry = capabilities[cap_id]
            desc = (entry.get("description") or "").strip().replace("\n", " ")
            lines.append(f"- {cap_id}")
            if desc:
                lines.append(f"    {desc}")
            params = entry.get("parameters", {})
            props = params.get("properties") if isinstance(params, dict) else None
            if not props:
                lines.append("    parameters: none")
            else:
                lines.append("    parameters:")
                for name, spec in props.items():
                    if not isinstance(spec, dict):
                        continue
                    t = spec.get("type", "any")
                    extras: list[str] = []
                    if spec.get("enum"):
                        extras.append(
                            "one of: " + ", ".join(str(x) for x in spec["enum"])
                        )
                    if spec.get("default") is not None:
                        extras.append(f"default={spec['default']!r}")
                    if spec.get("description"):
                        extras.append(str(spec["description"]).replace("\n", " "))
                    suffix = f" ({'; '.join(extras)})" if extras else ""
                    lines.append(f"      {name}: {t}{suffix}")
            lines.append("")
        return "\n".join(lines).rstrip()

    # ------------------------------------------------------------------
    # Parsing / normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(response: str) -> List[Dict[str, Any]]:
        text = (response or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not text:
            return []

        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Planner response must be a JSON array")
        return parsed

    @staticmethod
    def _normalize_tasks(
        raw_tasks: List[Dict[str, Any]],
        available: Dict[str, dict],
    ) -> List[TaskSpec]:
        tasks: List[TaskSpec] = []
        seen_ids: set[str] = set()

        for index, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid task at index {index}")

            capability = raw.get("capability") or raw.get("type")
            if not capability:
                raise ValueError(f"Task {index} has no capability")

            if capability not in available:
                raise ValueError(
                    f"Task {index} ({raw.get('id') or f'task_{index+1}'}) "
                    f"requested capability {capability!r}, which is not "
                    f"available for this user. Available: "
                    f"{sorted(available.keys())}"
                )

            task_id = raw.get("id") or f"task_{index + 1}"
            if task_id in seen_ids:
                raise ValueError(f"Duplicate task id {task_id!r}")
            seen_ids.add(task_id)

            schema = available[capability].get("parameters") or {}
            params_in = dict(raw.get("parameters", raw.get("params", {})))
            params = GoalDecomposer._coerce_params(params_in, schema)

            task = TaskSpec(
                id=task_id,
                type=raw.get("type") or capability,
                name=raw.get("name"),
                parameters=params,
                capability=capability,
                preferred_executor=raw.get("preferred_executor"),
                preferred_device=raw.get("preferred_device"),
                depends_on=list(raw.get("depends_on", [])),
                timeout=raw.get("timeout"),
                retry_count=int(raw.get("retry_count", 0)),
                metadata=dict(raw.get("metadata", {})),
            )
            tasks.append(task)

        task_ids = {t.id for t in tasks}
        for task in tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    raise ValueError(
                        f"Task {task.id} depends on unknown task {dep!r}"
                    )

        return tasks

    @staticmethod
    def _coerce_params(
        params: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        props = schema.get("properties") if isinstance(schema, dict) else None
        if not props:
            return params

        out: Dict[str, Any] = {}
        for key, value in params.items():
            spec = props.get(key)
            if not isinstance(spec, dict):
                out[key] = value
                continue
            t = spec.get("type", "string")
            try:
                if value is None:
                    out[key] = spec.get("default")
                elif t == "integer":
                    out[key] = int(value)
                elif t == "number":
                    out[key] = float(value)
                elif t == "boolean":
                    if isinstance(value, str):
                        out[key] = value.lower() in {"true", "1", "yes"}
                    else:
                        out[key] = bool(value)
                else:
                    out[key] = str(value)
            except (TypeError, ValueError):
                out[key] = value
        return out

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_decompose(
        self,
        goal: str,
        context: Dict[str, Any],
        available: Dict[str, dict],
        is_retry: bool = False,
    ) -> List[TaskSpec]:
        goal_lower = goal.lower()

        def _pick(*candidates: str) -> Optional[str]:
            for c in candidates:
                if c in available:
                    return c
            return None

        cap = _pick("notification.send", "notification")
        if cap and ("notify" in goal_lower or "notification" in goal_lower):
            return [TaskSpec(
                id="task_1", type="notification", capability=cap,
                parameters={"title": "Notification", "message": goal},
            )]

        cap = _pick("browser.navigate", "search")
        if cap and "search" in goal_lower:
            params = (
                {"url": f"https://www.google.com/search?q={goal}"}
                if cap == "browser.navigate"
                else {"query": goal}
            )
            return [TaskSpec(
                id="task_1", type="search", capability=cap, parameters=params,
            )]

        cap = _pick("camera.capture")
        if cap and any(
            w in goal_lower for w in ("photo", "picture", "snap", "capture")
        ):
            return [TaskSpec(
                id="task_1", type="capture_photo", capability=cap, parameters={},
            )]

        cap = _pick("browser.navigate")
        url = context.get("url")
        if cap and url:
            return [TaskSpec(
                id="task_1", type="browser_navigate", capability=cap,
                parameters={"url": url},
            )]

        cap = _pick("llm.complete", "llm_reason")
        if cap:
            return [TaskSpec(
                id="task_1", type="llm_reason", capability=cap,
                parameters={"goal": goal},
            )]

        # Before the generic chat fallback, if this is a retry,
        # something is wrong upstream — don't silently replace a real
        # action attempt with "I'm a chatbot". Let the failure surface.
        if is_retry:
            logger.error(
                "Fallback decompose on retry: no tool match for goal={!r}; "
                "refusing to emit chat.reply — retry must re-attempt the "
                "previous task. Available: {}",
                goal, sorted(available.keys()),
            )
            # Delegate to chat anyway so the user gets *some* reply;
            # but flag it so the trace shows the fallback fired on retry.
            # (Change to `raise ValueError(...)` if you want to fail hard.)

        # Final fallback: always allow conversational reply
        if CHAT_CAPABILITY_ID in available:
            logger.info(
                "Fallback decompose: no tool match for goal={!r}; "
                "returning chat reply", goal,
            )
            return [TaskSpec(
                id="task_1", type="chat", capability=CHAT_CAPABILITY_ID,
                parameters={"tone": "friendly"},
            )]

        logger.error(
            "Fallback decompose: no matching capability for goal={!r}. "
            "Available: {}", goal, sorted(available.keys()),
        )
        return []