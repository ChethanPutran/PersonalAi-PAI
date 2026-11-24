"""Adapters that connect the agent runtime to the rest of PAI.

Kept separate from the loop so the loop and runtime can be tested with fakes and never import
the orchestrator.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pai.llm.breakdown import TOOL_DATA, role_breakdown
from pai.llm.models import LLMMessage, LLMRequest


class OrchestratorCapabilitySource:
    """The user's live capabilities, run through the normal orchestrator action path."""

    def __init__(self, orchestrator: Any) -> None:
        self._o = orchestrator

    async def describe(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        caps = await self._o.capability_router.describe_capabilities_with_schema(user_id)
        return {cid: {"description": (info or {}).get("description", ""),
                      "parameters": (info or {}).get("parameters") or {"type": "object", "properties": {}}}
                for cid, info in caps.items()}

    async def execute(self, *, user_id: str, capability: str, params: Dict[str, Any], call_id: str,
                      session_id: Optional[str], agent_id: str, device_id: Optional[str]) -> Dict[str, Any]:
        # execute_action authorises (as an autonomous agent: sensitive actions need on-screen
        # approval), selects the device, creates a tracked task and waits for its result.
        # call_id is the request id: unique per call, so identical repeated actions really run.
        out = await self._o.execute_action(
            user_id=user_id, capability=capability, params=params, device_id=device_id,
            session_id=session_id, request_id=call_id, agent_id=agent_id)
        return unwrap_action_result(out)


class ToolFailed(RuntimeError):
    """The capability ran but reported failure (the agent sees this as an error, not as data)."""


def unwrap_action_result(out: Dict[str, Any]) -> Dict[str, Any]:
    """execute_action returns {"status": "completed", "result": <TaskResult>, ...} even when the
    task itself failed. Give the model the executor's output, not a repr of the whole TaskResult,
    and turn a reported failure into an error the loop shows as one."""
    res = out.get("result")
    if hasattr(res, "success") and hasattr(res, "output"):          # a TaskResult
        if not res.success:
            raise ToolFailed(str(getattr(res, "error", None) or "the action failed"))
        res = res.output
    return {"status": out.get("status"), "result": res, "device_id": out.get("device_id")}


class RouterLLMClient:
    def __init__(self, router: Any, route: Optional[str] = None) -> None:
        self._router = router
        self._route = route

    async def complete(self, messages: List[LLMMessage], *, temperature: float, max_tokens: int) -> str:
        response = await self._router.generate_request(
            LLMRequest(messages=messages, temperature=temperature, max_tokens=max_tokens,
                       # user-role messages in an agent run are the tool results fed back (plus the odd live message)
                       breakdown=role_breakdown(messages, {"user": TOOL_DATA})),
            route=self._route)
        return response.content


def guard_filters(prompt_guard: Any) -> Tuple[Callable[[str], str], Callable[[str], str]]:
    """(observation_filter, response_filter) backed by PromptGuard.

    Observations (tool output) are inspected like any tool-results block; what the agent says to
    the user is sanitised like a chat reply (secrets redacted, stray tool-call syntax removed).
    """
    def observation(text: str) -> str:
        return prompt_guard.inspect_request(user_text="", tool_results_block=text).tool_results_block

    def response(text: str) -> str:
        return prompt_guard.sanitize_response(raw_text=text).clean_text

    return observation, response
