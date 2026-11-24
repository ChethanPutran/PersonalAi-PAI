"""Parse a structured value (object/array) that may arrive as text.

LLMs sometimes emit a JSON object as a *string*, JSON or a Python-style repr
(``"{'lens': 'back'}"``). Accept those safely: JSON first, then ``ast.literal_eval`` (which only
evaluates literals — no names, calls or attribute access, so it cannot execute anything).
"""
from __future__ import annotations

import ast
import json
from typing import Any

_MAX_CHARS = 100_000


def parse_structured(value: Any) -> Any:
    """dict/list pass through; a string holding one is parsed; anything else is returned unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or len(text) > _MAX_CHARS or text[0] not in "{[":
        return value
    try:
        parsed = json.loads(text)
    except ValueError:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return value
    return parsed if isinstance(parsed, (dict, list)) else value
