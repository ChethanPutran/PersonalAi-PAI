"""Where a prompt's tokens went: system prompt, capabilities, history, memory, the user's words, tool data.

Providers report only ONE prompt-token total per call, so the split is an ESTIMATE: each named part of the
prompt is measured when the prompt is built (~4 characters per token), anything not named is "system_prompt"
(the template's own instructions), and the estimates are then scaled so they add up to the provider's real
`prompt_tokens`. The proportions are approximate; the total is exact.
"""
from __future__ import annotations

import contextvars
import math
from typing import Dict, Iterable, Mapping, Optional

SYSTEM_PROMPT = "system_prompt"     # the template's fixed instructions (and system messages)
CAPABILITIES = "capabilities"       # the list of capabilities/tools offered to the model
HISTORY = "history"                 # earlier conversation turns
MEMORY = "memory"                   # recalled memories
USER_PROMPT = "user_prompt"         # what the user just asked
TOOL_DATA = "tool_data"             # tool results / observations fed back to the model

# The call currently being made (set by LLMRouter around the provider call, read when usage is recorded).
current_breakdown: contextvars.ContextVar[Optional[Dict[str, int]]] = contextvars.ContextVar(
    "llm_breakdown", default=None)


def estimate_tokens(text: Optional[str]) -> int:
    """~4 characters per token (a common rule of thumb for English; exact counts need the model's tokenizer)."""
    return math.ceil(len(text) / 4) if text else 0


def prompt_breakdown(prompt: str, parts: Mapping[str, Optional[str]]) -> Dict[str, int]:
    """Estimated tokens per named part of `prompt`; the rest of the text is `system_prompt`.

    `parts` maps a section name to the text that was inserted verbatim into the template.
    Several parts with the same name are not supported (pass the concatenation)."""
    out: Dict[str, int] = {}
    used = 0
    for name, text in parts.items():
        if text:
            out[name] = out.get(name, 0) + estimate_tokens(text)
            used += len(text)
    rest = max(0, len(prompt) - used)
    if rest:
        out[SYSTEM_PROMPT] = out.get(SYSTEM_PROMPT, 0) + math.ceil(rest / 4)
    return out


def role_breakdown(messages: Iterable, roles: Optional[Mapping[str, str]] = None) -> Dict[str, int]:
    """Estimated tokens per message role. Default mapping: system -> system_prompt, assistant -> history,
    user -> user_prompt; pass `roles` to rename (agents map user messages to tool_data)."""
    mapping = {"system": SYSTEM_PROMPT, "assistant": HISTORY, "user": USER_PROMPT}
    mapping.update(roles or {})
    out: Dict[str, int] = {}
    for m in messages:
        name = mapping.get(getattr(m, "role", "user"), USER_PROMPT)
        out[name] = out.get(name, 0) + estimate_tokens(getattr(m, "content", ""))
    return {k: v for k, v in out.items() if v}


def scale_to(total: int, estimates: Mapping[str, int]) -> Dict[str, int]:
    """Scale the estimates so they sum to exactly `total` (largest-remainder rounding).

    No estimates -> {} (nothing to attribute). A zero estimate total -> everything is `system_prompt`."""
    est = {k: v for k, v in estimates.items() if v and v > 0}
    if total <= 0 or not est:
        return {}
    s = sum(est.values())
    raw = {k: v * total / s for k, v in est.items()}
    out = {k: int(math.floor(x)) for k, x in raw.items()}
    short = total - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:short]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}
