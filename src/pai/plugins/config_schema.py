"""Plugin CONFIGURATION: the settings a user chooses for a plugin on one device (not capability parameters).

A manifest declares them in ``configSchema`` (a JSON-Schema subset)::

    "configSchema": {"type": "object", "properties": {
        "allowedPaths": {"type": "array", "items": {"type": "string"}, "title": "...", "description": "...",
                         "default": [...], "x-pai": {"widget": "paths", "suggestions": [{"label", "path"}]}},
        "readOnly":     {"type": "boolean", "default": false}}}

Values are stored per (device, plugin), validated HERE (server side, whatever the client sent), and
enforced by the backend (authorization scope) and again by the device. Pure module: no I/O.

Supported property types: string (enum, maxLength, pattern), boolean, integer / number (minimum, maximum),
array of string (maxItems). Unknown keys are rejected so a typo never silently does nothing.
``x-pai.widget == "paths"`` marks a list of filesystem paths: each must be absolute (or start with ``~/``),
contain no NUL and no ``..`` segment; they are normalised and de-duplicated.
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Dict, List, Mapping, Optional

MAX_ARRAY_ITEMS = 200
MAX_STRING = 4096


class ConfigError(ValueError):
    """Invalid configuration; ``errors`` maps a key to a plain-words problem."""

    def __init__(self, errors: Dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def properties(schema: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    props = (schema or {}).get("properties") if isinstance(schema, Mapping) else None
    return {k: dict(v) for k, v in props.items() if isinstance(v, Mapping)} if isinstance(props, Mapping) else {}


def defaults(schema: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {k: v["default"] for k, v in properties(schema).items() if "default" in v}


def effective(schema: Optional[Mapping[str, Any]], stored: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Defaults overlaid with what the user saved (what a plugin should actually use)."""
    out = defaults(schema)
    out.update({k: v for k, v in (stored or {}).items() if k in properties(schema)})
    return out


def normalize_path(raw: str) -> str:
    p = raw.strip()
    if not p or "\x00" in p:
        raise ValueError("not a valid path")
    if not (p.startswith("/") or p == "~" or p.startswith("~/")):
        raise ValueError("must be an absolute path (start with / or ~/)")
    if ".." in p.split("/"):
        raise ValueError("must not contain '..'")
    norm = posixpath.normpath(p)
    return norm if norm != "." else "/"


def _check(key: str, spec: Mapping[str, Any], value: Any) -> Any:
    t = spec.get("type")
    if t == "boolean":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value
    if t in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (t == "integer" and not isinstance(value, int)):
            raise ValueError(f"must be {'a whole number' if t == 'integer' else 'a number'}")
        if "minimum" in spec and value < spec["minimum"]:
            raise ValueError(f"must be at least {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValueError(f"must be at most {spec['maximum']}")
        return value
    if t == "string":
        if not isinstance(value, str):
            raise ValueError("must be text")
        if len(value) > min(int(spec.get("maxLength", MAX_STRING)), MAX_STRING):
            raise ValueError("is too long")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"must be one of {', '.join(map(str, spec['enum']))}")
        if spec.get("pattern") and not re.search(spec["pattern"], value):
            raise ValueError("has the wrong format")
        return value
    if t == "array":
        if not isinstance(value, list):
            raise ValueError("must be a list")
        limit = min(int(spec.get("maxItems", MAX_ARRAY_ITEMS)), MAX_ARRAY_ITEMS)
        if len(value) > limit:
            raise ValueError(f"has more than {limit} entries")
        if not all(isinstance(i, str) for i in value):
            raise ValueError("must be a list of text values")
        if (spec.get("x-pai") or {}).get("widget") == "paths":
            return list(dict.fromkeys(normalize_path(i) for i in value))
        return [_check(key, spec.get("items") or {"type": "string"}, i) for i in value]
    raise ValueError(f"unsupported setting type {t!r}")


def validate(schema: Optional[Mapping[str, Any]], values: Any) -> Dict[str, Any]:
    """Return the cleaned values, or raise ConfigError. ``None`` for a key resets it to the default."""
    props = properties(schema)
    if not isinstance(values, Mapping):
        raise ConfigError({"": "settings must be an object"})
    errors: Dict[str, str] = {}
    clean: Dict[str, Any] = {}
    for key, value in values.items():
        spec = props.get(key)
        if spec is None:
            errors[str(key)] = "is not a setting of this plugin"
            continue
        if value is None:
            continue                                   # reset: falls back to the default
        try:
            clean[key] = _check(key, spec, value)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ConfigError(errors)
    return clean


def path_policy(values: Mapping[str, Any]) -> Dict[str, Any]:
    """What the file-style enforcement needs, from effective settings."""
    return {
        "allowed": [p for p in values.get("allowedPaths") or [] if isinstance(p, str)],
        "denied": [p for p in values.get("deniedPaths") or [] if isinstance(p, str)],
        "read_only": bool(values.get("readOnly", False)),
    }


READ_ONLY_BLOCKED: List[str] = ["files.write", "files.delete", "files.move", "files.copy"]
