"""Goal decomposition into declarative task specifications."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from pai.core.lifecycle import Lifecycle
from pai.utils.structured import parse_structured
from pai.llm.breakdown import CAPABILITIES, HISTORY, MEMORY, USER_PROMPT, prompt_breakdown
from pai.llm.models import LLMMessage, LLMRequest
from pai.llm.provider import LLMProvider
from pai.llm.router import LLMRouter
from pydantic import BaseModel, Field

from pai.common.enums.task import TaskType
from pai.memory.manager import MemoryManager, _looks_like_retry
from pai.observability.tracer import get_tracer
from pai.orchestration.capability_router import CapabilityRouter
from pai.planning.capability_select import missing_from, restrict, select_capabilities
from pai.planning.models import TaskSpec
import re

from pai.prompts.registry import PromptRegistry, PromptTemplate
from pai.prompts.tracker import PromptVersionTracker
from pai.security.guards.llm_guard import PromptGuard
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
            "intent": {
                "type": "string",
                "enum": ["reply", "clarify"],
                "default": "reply",
                "description": (
                    "'clarify' = ask the user ONE question because a required value is "
                    "missing or ambiguous (set question and options)"
                ),
            },
            "question": {
                "type": "string",
                "description": "The clarifying question (only with intent=clarify)",
            },
            "options": {
                "type": "array",
                "description": "Valid answers to offer (only with intent=clarify)",
            },
        },
    },
    "task_type": TaskType.ONE_SHOT.value,
    "timeout_seconds": None,
    "targets": [
        {
            "device_id": "server",
            "executor_id": "llm_executor",
            "device_type": "server",
        },
    ],
}



class ParameterIssue(BaseModel):
    """One required/enum parameter the planner could not resolve from the user's words."""

    task_id: str
    capability: str
    parameter: str
    reason: str                                   # "missing" | "invalid"
    value: Any = None
    options: List[str] = Field(default_factory=list)
    description: str = ""

    def question(self) -> str:
        label = self.parameter.replace("_", " ")
        if self.options:
            return (f"Which {label} should I use for {self.capability}? "
                    f"({' / '.join(self.options)})")
        hint = f" ({self.description})" if self.description else ""
        return f"What {label} should I use for {self.capability}?{hint}"


class ClarificationNeeded(ValueError):
    """Planning is blocked on information only the user can give.

    Subclasses ValueError so code that treats any plan-validation failure as fatal still
    works; `GoalDecomposer.decompose` catches it and turns it into a clarifying chat reply.
    """

    def __init__(self, issues: List[ParameterIssue]):
        self.issues = issues
        super().__init__("; ".join(
            f"{i.capability}: {i.parameter} {i.reason}" for i in issues))


# Output budget for the plan. Reasoning models (e.g. the free OpenRouter ones) spend most of it on
# hidden reasoning and return null content when it runs out, so this must be generous.
PLANNER_MAX_TOKENS = 6000


class GoalDecomposer(Lifecycle):
    def __init__(
        self,
        llm: LLMRouter,
        capability_router: CapabilityRouter,
        memory_manager: MemoryManager,
        chat_repo: ChatRepository,
        prompt_registry: PromptRegistry,
        prompt_tracker: PromptVersionTracker,
        prompt_guard: PromptGuard,
        max_tokens: int = PLANNER_MAX_TOKENS,
    ):
        self.max_tokens = max_tokens
        self.llm = llm
        self.capability_router = capability_router
        self.memory_manager = memory_manager
        self.chat_repo = chat_repo
        self.prompts = prompt_registry
        self.prompt_tracker = prompt_tracker
        self.prompt_guard = prompt_guard
        # Set by the orchestrator from config (orchestration.capability_retrieval); see planning/capability_select.py.
        self.capability_retrieval = False
        self.capability_retrieval_families = 4

    async def initialize(self) -> None:
        logger.info("GoalDecomposer initialized")

        for prompt_id in ["planning.goal_decomposition", "chat.reply"]:
            meta = self.prompts._load_meta(prompt_id)
            current = meta["current"]
            template = self.prompts.get(prompt_id, version=current)
            # Cheap sanity: render with dummies to catch undeclared vars now,
            # not at 3am when the first user runs a goal.
            declared = set(meta.get("variables", {}))
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
        *,
        _show_all: bool = False,
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

            is_retry = _looks_like_retry(goal or "")
            span.set_attribute("is_retry", is_retry)

            # Capability retrieval: show the planner only what fits the request. Validation below still uses
            # the FULL set, so a plan that names a capability we did not show is detected and re-planned.
            shown = capabilities
            if self.capability_retrieval and not _show_all and not is_retry:
                selection = select_capabilities(
                    goal or "", capabilities, top_families=self.capability_retrieval_families,
                    sticky=context.get("last_capabilities") or (),
                )
                # A short follow-up ("and another one") means what the history says, not what it spells.
                if selection.confident and history_block and len(goal.split()) < 4:
                    selection = type(selection)(sorted(capabilities), False, "short_followup")
                shown = restrict(capabilities, selection)
                span.set_attribute("retrieval", selection.reason)
                span.set_attribute("retrieval_shown", len(shown))
                span.set_attribute("retrieval_of", len(capabilities))

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

            prompt_parts: Dict[str, str] = {}
            prompt, template = self._render_prompt(
                sections=prompt_parts,
                goal=goal,
                context=context,
                capabilities=shown,
                memory_block=inspection.memory_block,
                history_block=inspection.history_block,
                is_retry=is_retry,
                user_tz=user_tz,
            )
            span.set_attribute("prompt_length", len(prompt))
            prompt_tokens_by_part = prompt_breakdown(prompt, prompt_parts)
            span.set_attribute("prompt_tokens_estimate", prompt_tokens_by_part)
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
                budget = self.max_tokens
                llm_response = await self.llm.generate_request(LLMRequest(
                    messages=[LLMMessage(role="user", content=prompt)],
                    temperature=0.1,
                    max_tokens=budget,
                    breakdown=prompt_tokens_by_part,
                ), route="planner")
                if llm_response.finish_reason == "length":
                    # Reasoning ate the budget: retry once with double before giving up.
                    budget *= 2
                    logger.warning("Planner output truncated; retrying with max_tokens={}", budget)
                    llm_response = await self.llm.generate_request(LLMRequest(
                        messages=[LLMMessage(role="user", content=prompt)],
                        temperature=0.1,
                        max_tokens=budget,
                        breakdown=prompt_tokens_by_part,
                    ), route="planner")
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
                    f"Planner LLM response was truncated at max_tokens={budget}. "
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
                unseen = missing_from((t.capability for t in tasks), shown)
                if unseen:
                    # The plan used something the planner was not shown its schema for: plan again with all.
                    logger.info("Capability retrieval missed {}; re-planning with the full list", unseen)
                    span.set_attribute("retrieval_missed", unseen)
                    return await self.decompose(goal, context, _show_all=True)
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
            except ClarificationNeeded as exc:
                logger.info("Planner needs clarification: {}", exc)
                span.set_attribute("clarification", True)
                return [self._clarification_task(exc.issues)]
            except ValueError as exc:
                logger.error("Plan validation failed: {}", exc)
                span.set_error(f"validation_failed: {exc}")
                raise

            # 7. Fallback
            fallback_tasks = self._fallback_decompose(
                goal, context, capabilities, is_retry=is_retry
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
        sections: Optional[Dict[str, str]] = None,
    ) -> tuple[str, "PromptTemplate"]:
        template = self.prompts.get("planning.goal_decomposition")
        capabilities_block = self._render_capabilities(capabilities)
        if sections is not None:        # the parts inserted verbatim, for the per-part token breakdown
            sections.update({CAPABILITIES: capabilities_block, HISTORY: history_block,
                             MEMORY: memory_block, USER_PROMPT: goal})

        rendered = template.render(
            goal=goal,
            capabilities_block=capabilities_block,
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
                required = set(params.get("required") or [])
                for name, spec in props.items():
                    if not isinstance(spec, dict):
                        continue
                    t = spec.get("type", "any")
                    extras: list[str] = []
                    if name in required and spec.get("default") is None:
                        extras.append("required")
                    if spec.get("enum"):
                        extras.append(
                            "one of: " + ", ".join(str(x) for x in spec["enum"])
                        )
                    if spec.get("default") is not None:
                        extras.append(f"default={spec['default']!r}")
                    if spec.get("askIfUnspecified"):
                        extras.append("ASK the user if not specified")
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
    def _validate_params(
        capability: str,
        params: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> None:
        """Raise ValueError if params violate the capability schema.

        Only covers schema-level constraints — required, enum, type.
        Domain-level constraints (shell whitelist, filesystem roots) are
        validated by the plugin at invoke time.
        """
        props = schema.get("properties") or {}
        required = schema.get("required") or []

        for key in required:
            if key not in params:
                raise ValueError(
                    f"{capability}: missing required parameter {key!r}"
                )

        for key, value in params.items():
            spec = props.get(key)
            if not isinstance(spec, dict):
                continue  # unknown param — plugin decides; some plugins accept extras

            # Enum check — this is the big one for LLM hallucinations
            enum = spec.get("enum")
            if enum and value not in enum:
                raise ValueError(
                    f"{capability}: parameter {key!r} = {value!r} is not one "
                    f"of {enum}"
                )

            # Type check (only fail on obvious mismatch, not on lossless coercion)
            t = spec.get("type")
            if t == "integer" and not isinstance(value, int):
                raise ValueError(
                    f"{capability}: parameter {key!r} must be integer, got {type(value).__name__}"
                )
            if t == "string" and not isinstance(value, str):
                raise ValueError(
                    f"{capability}: parameter {key!r} must be string, got {type(value).__name__}"
                )
            if t == "boolean" and not isinstance(value, bool):
                raise ValueError(
                    f"{capability}: parameter {key!r} must be boolean, got {type(value).__name__}"
                )
    
    @staticmethod
    def _check_precondition(
        capability: str,
        params: Dict[str, Any],
        cap_schema: Dict[str, Any],
        plugin_metadata: Dict[str, Any],
    ) -> None:
        """Enforce domain-level preconditions declared in the schema.

        Currently supports `command_prefix` (shell) — the plugin still
        enforces this at invoke time; this is a fast-fail layer so the
        user isn't asked to confirm a command that will definitely be
        rejected.
        """
        pre = cap_schema.get("precondition") or {}
        if pre.get("type") != "command_prefix":
            return

        field = pre.get("field", "command")
        command = params.get(field)
        if not command:
            return

        # Resolve allowed prefixes from plugin metadata
        ref = pre.get("allowed_prefixes_ref", "metadata.allowed_prefixes")
        allowed = plugin_metadata.get("allowed_prefixes", [])

        # Command must contain no metacharacters
        forbidden = ";&|<>$`\n\r"
        if any(c in forbidden for c in command):
            raise ValueError(
                f"{capability}: command contains forbidden metacharacters"
            )

        # Must match a whitelisted prefix
        trimmed = command.strip()
        ok = any(
            trimmed == prefix or trimmed.startswith(prefix + " ")
            for prefix in allowed
        )
        if not ok:
            raise ValueError(
                f"{capability}: command {trimmed[:60]!r} is not whitelisted. "
                f"Allowed prefixes: {allowed}"
            )
        
    @staticmethod
    def _normalize_tasks(
        raw_tasks: List[Dict[str, Any]],
        available: Dict[str, dict],
    ) -> List[TaskSpec]:
        """
        Layer 1 — Schema-level validation 
        Normalize raw task dicts into TaskSpec objects, validating against
        the available capabilities and their schemas.
        Raises ValueError on any validation error.
        """
        

        # TODO: Check if the capability requires confirmation or HTL add requires_confirmation to the task spec and set it accordingly. 
        # This is a temporary fix until the capability spec is updated to include requires_confirmation.
        tasks: List[TaskSpec] = []
        issues: List[ParameterIssue] = []
        seen_ids: set[str] = set()

        for index, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid task at index {index}")

            capability = raw.get("capability")
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
            params_raw = raw.get("parameters") or raw.get("params") or {}
            params_in = dict(params_raw) if isinstance(params_raw, dict) else {}
            params = GoalDecomposer._coerce_params(params_in, schema)

            # Missing / invalid required-or-enum values are the user's to decide, not ours to
            # guess: collect them and ask (defaults still win; enum case is forgiven).
            params, found = GoalDecomposer._resolve_parameter_issues(
                task_id, capability, params, schema)
            if found:
                issues.extend(found)
                continue

            # Validate parameters against the capability schema.
            GoalDecomposer._validate_params(capability, params, schema)

            # Check domain-level preconditions (e.g., shell command prefix).
            plugin_metadata = available[capability].get("metadata") or {}
            GoalDecomposer._check_precondition(
                capability, params, schema, plugin_metadata
            )

            # Execution metadata is owned by the capability manifest. Whatever the LLM wrote
            # in "type" / "timeout" is ignored: an adversarial or sloppy plan can neither turn
            # a stream into a one-shot nor lift a deadline.
            if raw.get("type") is not None or raw.get("timeout") is not None:
                logger.debug("Planner supplied type/timeout for {}; ignored (manifest wins)",
                             capability)
            entry = available[capability]
            task_type = GoalDecomposer._manifest_task_type(entry)

            name = raw.get("name")
            if not name or not isinstance(name, str):
                name = f"{capability}_task"

            spec_kwargs: Dict[str, Any] = {}
            if entry.get("timeout_seconds"):
                spec_kwargs["timeout"] = float(entry["timeout_seconds"])
            if entry.get("retryable") is not None:
                spec_kwargs["retryable"] = bool(entry["retryable"])

            task = TaskSpec(
                id=task_id,
                type=task_type,
                name=name,
                capability=capability,
                parameters=params,
                depends_on=list(raw.get("depends_on") or []),
                **spec_kwargs,
            )
            tasks.append(task)

        if issues:
            raise ClarificationNeeded(issues)

        task_ids = {t.id for t in tasks}
        for task in tasks:
            for dep in task.depends_on:
                if dep not in task_ids:
                    raise ValueError(
                        f"Task {task.id} depends on unknown task {dep!r}"
                    )

        return tasks

    @staticmethod
    def _manifest_task_type(entry: Dict[str, Any]) -> TaskType:
        try:
            return TaskType(entry.get("task_type") or TaskType.ONE_SHOT.value)
        except ValueError:
            return TaskType.ONE_SHOT

    @staticmethod
    def _resolve_parameter_issues(
        task_id: str,
        capability: str,
        params: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[ParameterIssue]]:
        """Fill defaults, forgive enum case, and report what only the user can decide."""
        props = (schema.get("properties") if isinstance(schema, dict) else None) or {}
        required = list((schema.get("required") if isinstance(schema, dict) else None) or [])
        out = {k: v for k, v in params.items() if v is not None}
        issues: List[ParameterIssue] = []

        def _options(spec: Dict[str, Any]) -> List[str]:
            return [str(x) for x in (spec.get("enum") or [])]

        for key in required:
            spec = props.get(key) if isinstance(props.get(key), dict) else {}
            blank = key not in out or (isinstance(out[key], str) and not out[key].strip())
            if not blank:
                continue
            if spec.get("default") is not None:
                out[key] = spec["default"]          # a default always beats asking
                continue
            issues.append(ParameterIssue(
                task_id=task_id, capability=capability, parameter=key, reason="missing",
                options=_options(spec), description=str(spec.get("description") or "")[:120]))

        for key, value in list(out.items()):
            spec = props.get(key)
            if not isinstance(spec, dict) or not spec.get("enum") or value in spec["enum"]:
                continue
            match = next((e for e in spec["enum"]
                          if str(e).lower() == str(value).strip().lower()), None)
            if match is not None:
                out[key] = match
                continue
            issues.append(ParameterIssue(
                task_id=task_id, capability=capability, parameter=key, reason="invalid",
                value=str(value)[:60], options=_options(spec),
                description=str(spec.get("description") or "")[:120]))
        return out, issues

    @staticmethod
    def _clarification_task(issues: List[ParameterIssue]) -> TaskSpec:
        """A single chat.reply that asks the user the one most important question."""
        first = issues[0]
        return TaskSpec(
            id="task_1",
            name="Ask for missing detail",
            type=TaskType.ONE_SHOT,
            capability=CHAT_CAPABILITY_ID,
            parameters={
                "tone": "friendly",
                "intent": "clarify",
                "question": first.question(),
                "options": list(first.options),
            },
        )

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
                elif t in ("object", "array"):
                    # Keep structured values structured (a dict used to be turned into its
                    # repr string here). A string holding JSON / a Python literal is parsed.
                    out[key] = parse_structured(value)
                elif t == "string" and isinstance(value, (int, float, bool)):
                    out[key] = str(value)
                else:
                    out[key] = value        # never corrupt what we do not understand
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
                id="task_1", type=self._manifest_task_type(available[cap]), capability=cap,
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
                id="task_1", type=self._manifest_task_type(available[cap]), capability=cap, parameters=params,
            )]

        cap = _pick("camera.capture")
        if cap and any(
            w in goal_lower for w in ("photo", "picture", "snap", "capture")
        ):
            return [TaskSpec(
                id="task_1", type=self._manifest_task_type(available[cap]), capability=cap, parameters={},
            )]

        cap = _pick("browser.navigate")
        url = context.get("url")
        if cap and url:
            return [TaskSpec(
                id="task_1", type=self._manifest_task_type(available[cap]), capability=cap,
                parameters={"url": url},
            )]

        cap = _pick("llm.complete", "llm_reason")
        if cap:
            return [TaskSpec(
                id="task_1", type=self._manifest_task_type(available[cap]), capability=cap,
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
                id="task_1", type=self._manifest_task_type(available[CHAT_CAPABILITY_ID]), capability=CHAT_CAPABILITY_ID,
                parameters={"tone": "friendly"},
            )]

        logger.error(
            "Fallback decompose: no matching capability for goal={!r}. "
            "Available: {}", goal, sorted(available.keys()),
        )
        return []