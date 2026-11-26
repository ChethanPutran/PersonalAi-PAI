"""Decide, before any planning, whether a turn is plain conversation or needs tools.

Why: the planner is an LLM call that must return a strict JSON plan (3.5 to 6 s on a strong model),
and for "hi, how are you?" it returns one ``chat.reply`` task and the reply is then written by a
SECOND call. A conversational turn does not need the first call at all. Skipping it takes the time to the
first reply token from ~4.5 s to about one fast-model round trip.

How (cheapest check first, always failing toward the planner, which can do everything):

1. rules (no LLM, ~0 ms): small talk with no action wording -> CHAT; clear action wording -> ACT.
2. otherwise one tiny classification call on the fast chat model (a few tokens out, hard timeout).
   Any error, timeout or unexpected answer -> ACT.

A wrong CHAT is the costly mistake (the user asks for something and just gets a chat answer), so the
rules and the classifier prompt lean heavily toward ACT.
"""
from __future__ import annotations

import asyncio
import re
import time
from enum import StrEnum
from typing import Any, Optional

from loguru import logger

from pai.llm.models import LLMMessage, LLMRequest


class TurnRoute(StrEnum):
    CHAT = "chat"      # answer conversationally, no tools, no planner
    ACT = "act"        # plan and run capabilities (the full pipeline)


# Words that suggest the user wants something DONE or looked up (devices, files, schedules, the web ...).
_ACTION = re.compile(
    r"\b(take|capture|snap|photo|picture|selfie|screenshot|screen|open|launch|close|quit|start|stop|play|pause|resume|"
    r"skip|next|previous|mute|unmute|volume|brightness|set|remind|reminder|schedule|alarm|timer|calendar|meeting|"
    r"send|email|e-mail|text|sms|call|dial|message|share|search|google|find|look ?up|browse|visit|go to|download|"
    r"upload|save|delete|remove|rename|move|copy|paste|create|make|write|read|edit|run|execute|install|uninstall|"
    r"turn (?:on|off)|switch|toggle|enable|disable|connect|disconnect|pair|record|click|type|press|scroll|drag|"
    r"list|show|check|status|battery|wifi|bluetooth|ip address|location|where am i|directions|navigate|weather|"
    r"news|price|stock|score|translate|book|order|buy|pay|file|folder|app|application|clipboard|notification|"
    r"my (?:phone|laptop|computer|desktop|device|devices|files|photos|schedule|reminders|calendar))\b",
    re.I)

# Short pleasantries and questions about the assistant itself.
_SMALL_TALK = re.compile(
    r"^\W*(?:hi+|hello+|hey+|hola|yo|sup|howdy|good (?:morning|afternoon|evening|night)|thanks?(?: you)?|thank you|"
    r"ok(?:ay)?|cool|great|nice|awesome|perfect|got it|sure|yes|no|yep|nope|lol|haha|bye|goodbye|see you|"
    r"how are you|how'?s it going|how do you do|what'?s up|who are you|what are you|what is your name|"
    r"what can you do|what do you do|tell me about yourself|are you there)\b[\w\s,.!?'’-]{0,48}$",
    re.I)

_MAX_RULE_CHARS = 70

_CLASSIFY_PROMPT = """You route messages for a personal assistant that can control the user's devices.
Answer with exactly one word.
CHAT: the message is only conversation, an opinion, general knowledge, a joke, an explanation, or small talk,
      and nothing needs to be done on a device or looked up live.
ACT: the user wants anything done or fetched: using a device, app, file, camera, schedule, reminder, message, the
     web, the user's own data or settings, live information, or you are not sure.
When in doubt answer ACT.

Message: {goal}
Answer:"""


# One word is needed, but some models spend a few tokens on a leading newline/space or a tag before it;
# at 4 the answer was cut to "" (finish_reason=length) and every message fell back to the slow planner.
_CLASSIFIER_MAX_TOKENS = 24


def rule_route(goal: str) -> Optional[TurnRoute]:
    """The no-LLM decision, or None when the rules cannot tell."""
    text = (goal or "").strip()
    if not text:
        return TurnRoute.ACT
    if _ACTION.search(text):
        return TurnRoute.ACT
    if len(text) <= _MAX_RULE_CHARS and _SMALL_TALK.match(text):
        return TurnRoute.CHAT
    return None


class FastRouter:
    """Rules first, then a tiny LLM call; fails toward ACT."""

    def __init__(self, llm: Any, *, enabled: bool = True, timeout_seconds: float = 2.5,
                 route: str = "chat") -> None:
        self._llm = llm
        self.enabled = enabled
        self._timeout = timeout_seconds
        self._route = route                    # the fast model route (latency matters more than depth)

    async def decide(self, goal: str) -> tuple[TurnRoute, str]:
        """(route, how): how is "disabled" | "rule" | "llm" | "llm_failed" for the trace."""
        if not self.enabled or self._llm is None:
            return TurnRoute.ACT, "disabled"
        by_rule = rule_route(goal)
        if by_rule is not None:
            return by_rule, "rule"
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(self._llm.generate_request(
                LLMRequest(messages=[LLMMessage(role="user", content=_CLASSIFY_PROMPT.format(goal=goal[:400]))],
                           temperature=0.0, max_tokens=_CLASSIFIER_MAX_TOKENS), route=self._route), timeout=self._timeout)
        except Exception as exc:               # timeout, provider error, ...: the planner can always handle it
            logger.debug("fast route classifier failed after {:.0f}ms: {}", (time.perf_counter() - t0) * 1000, exc)
            return TurnRoute.ACT, "llm_failed"
        answer = (getattr(resp, "content", "") or "").strip().upper()
        return (TurnRoute.CHAT if answer.startswith("CHAT") else TurnRoute.ACT), "llm"
