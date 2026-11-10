from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{35}"), "AIza***"),
    (re.compile(
        r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    ), "***JWT***"),
]

_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "password", "secret",
    "token", "access_token", "refresh_token", "private_key",
    "client_secret", "session_token",
}


def redact_text(text: str) -> str:
    if not text:
        return text
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(value: Any, _key: str = "", depth: int = 0) -> Any:
    if depth > 8:
        return "<depth-limit>"
    if isinstance(value, str):
        if _key.lower() in _SENSITIVE_KEYS:
            return "***"
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v, k, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _key, depth + 1) for v in value]
    return value


def truncate(value: Any, max_len: int = 4000) -> Any:
    if isinstance(value, str):
        if len(value) <= max_len:
            return value
        return value[:max_len] + f"…[+{len(value) - max_len}]"
    if isinstance(value, dict):
        return {k: truncate(v, max_len) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [truncate(v, max_len) for v in value]
    return value