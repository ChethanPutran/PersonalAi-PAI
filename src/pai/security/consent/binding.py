"""Parameter binding for consent grants, and the canonical-parameters hashing shared with the
scheduler's per-schedule consent (one definition of "same parameters").

A grant is always bound to (user, plugin, capability, device). On top of that the user picks
how tightly it is bound to the call's PARAMETERS:

  any    the capability on that device, whatever the parameters;
  exact  the sha256 of ALL parameters (canonical JSON) — change anything and it asks again;
  keys   the sha256 of only the listed parameters — the others may vary freely.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")
MAX_BOUND_KEYS = 20


def canonical_parameters(parameters: Mapping[str, Any] | None) -> str:
    """Stable JSON: same parameters => same text, regardless of key order or spacing."""
    return json.dumps(parameters or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


class BindingMode(StrEnum):
    ANY = "any"
    EXACT = "exact"
    KEYS = "keys"


class ParamBinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: BindingMode = BindingMode.EXACT
    keys: List[str] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def _plain_keys(cls, keys: List[str]) -> List[str]:
        bad = [k for k in keys if not isinstance(k, str) or not _KEY_RE.match(k)]
        if bad:
            raise ValueError(f"invalid parameter name(s): {bad[:3]}")
        if len(keys) > MAX_BOUND_KEYS:
            raise ValueError(f"at most {MAX_BOUND_KEYS} parameters can be bound")
        return sorted(set(keys))

    @model_validator(mode="after")
    def _keys_match_mode(self) -> "ParamBinding":
        if self.mode == BindingMode.KEYS and not self.keys:
            raise ValueError("binding mode 'keys' needs at least one parameter name")
        if self.mode != BindingMode.KEYS and self.keys:
            raise ValueError(f"binding mode '{self.mode.value}' takes no parameter names")
        return self

    def hash_of(self, parameters: Mapping[str, Any] | None) -> str:
        """The value a grant stores / a later call is compared against ('' for ``any``)."""
        params = dict(parameters or {})
        if self.mode == BindingMode.ANY:
            return ""
        subject = params if self.mode == BindingMode.EXACT else {k: params.get(k) for k in self.keys}
        return hashlib.sha256(canonical_parameters(subject).encode("utf-8")).hexdigest()

    def unknown_keys(self, parameters: Mapping[str, Any] | None) -> List[str]:
        """Bound names that the call does not even have (a grant on them would be meaningless)."""
        have = set(parameters or {})
        return [k for k in self.keys if k not in have]

    def matches(self, stored_hash: str, parameters: Mapping[str, Any] | None) -> bool:
        return self.mode == BindingMode.ANY or self.hash_of(parameters) == stored_hash


DEFAULT_BINDING = ParamBinding(mode=BindingMode.EXACT)

# Capabilities whose remembered approval must name these parameters: approving `ls` for a session
# must never approve `rm`. Enforced where grants are created AND where they are matched.
BOUND_PARAMETERS: dict[str, tuple[str, ...]] = {"shell.exec": ("command",)}


def required_keys(capability: Optional[str]) -> tuple[str, ...]:
    return BOUND_PARAMETERS.get(capability or "", ())


def binding_allowed(capability: Optional[str], binding: "ParamBinding") -> bool:
    """False when ``binding`` would let a bound capability vary the parameters it must pin."""
    need = required_keys(capability)
    if not need:
        return True
    if binding.mode == BindingMode.EXACT:
        return True                                   # pins everything, including the command
    return binding.mode == BindingMode.KEYS and set(need) <= set(binding.keys)


def default_binding_for(capability: Optional[str]) -> "ParamBinding":
    need = required_keys(capability)
    return ParamBinding(mode=BindingMode.KEYS, keys=list(need)) if need else DEFAULT_BINDING


_SENSITIVE = re.compile(r"(token|password|passwd|secret|api[_-]?key|authorization|credential|cookie|private[_-]?key)", re.I)
PREVIEW_MAX = 120


def preview_parameters(parameters: Mapping[str, Any] | None, only: Optional[List[str]] = None) -> list:
    """(name, value_preview) pairs for the approval dialog. Secret-looking values are masked,
    long values truncated. Returns ParamPreview models (imported lazily to avoid a cycle)."""
    from pai.security.consent.models import ParamPreview
    out = []
    for name in sorted(parameters or {}):
        if only is not None and name not in only:
            continue
        value = (parameters or {})[name]
        if _SENSITIVE.search(name):
            text = "••••••"
        else:
            text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
            text = text if len(text) <= PREVIEW_MAX else text[: PREVIEW_MAX - 1] + "…"
        out.append(ParamPreview(name=name, value_preview=text))
    return out
