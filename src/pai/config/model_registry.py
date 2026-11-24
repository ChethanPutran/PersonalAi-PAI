"""The model registry: `config/models.yaml`, the one place that knows which models exist, what they can do and
what they cost.

    llm[]             name, provider, tool_use/reasoning/vision, context_length, max_output_length, prices per million
    speech.stt[]      per_minute_cost                (billed per second of audio)
    speech.tts[]      per_1k_chars_cost              (billed per input character)
    embeddings[], decision[]

(Older layouts still load: `llm.chat[]` and `models.embeddings[]`.)

Used for: LLM cost (token prices), voice cost (STT/TTS prices), startup checks that the configured models exist and
can do what their role needs (a planner/agent model must support tools), and the admin "Models & prices" table.
Prices in `llm_pricing` (config.yaml) still win: that is the per-deployment override.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from pai.config.models.llm import ModelPrice

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMB]?)\s*$", re.I)
_MULT = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


class ModelRegistryError(ValueError):
    """models.yaml is malformed (every problem is listed in the message)."""


def parse_size(value: Any) -> Optional[int]:
    """`33K` -> 33000, `1.1M` -> 1100000, `4096` -> 4096, None -> None. Anything else is an error."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    m = _SIZE.match(str(value))
    if not m:
        raise ValueError(f"{value!r} is not a size (use 4096, 33K, 1.1M)")
    return int(round(float(m.group(1)) * _MULT[m.group(2).upper()]))


def _flag(value: Any) -> Optional[bool]:
    """`enabled`/`disabled` (or a bool) -> bool; absent -> None (unknown)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("enabled", "true", "yes", "on"):
        return True
    if v in ("disabled", "false", "no", "off"):
        return False
    raise ValueError(f"{value!r} is not enabled/disabled")


class _Base(BaseModel):
    name: str = Field(min_length=1)
    provider: Optional[str] = None


class LLMModelInfo(_Base):
    tool_use: Optional[bool] = None
    reasoning: Optional[bool] = None
    vision: Optional[bool] = None
    context_length: Optional[int] = None
    max_output_length: Optional[int] = None
    input_per_million: Optional[float] = Field(default=None, ge=0)
    output_per_million: Optional[float] = Field(default=None, ge=0)
    cache_read_per_million: Optional[float] = Field(default=None, ge=0)
    cache_write_per_million: Optional[float] = Field(default=None, ge=0)

    @property
    def price(self) -> Optional[ModelPrice]:
        if self.input_per_million is None and self.output_per_million is None:
            return None
        return ModelPrice(input_per_million=self.input_per_million or 0.0, output_per_million=self.output_per_million or 0.0)


class SttModelInfo(_Base):
    per_minute_cost: Optional[float] = Field(default=None, ge=0)


class TtsModelInfo(_Base):
    per_1k_chars_cost: Optional[float] = Field(default=None, ge=0)


class ModelRegistry(BaseModel):
    llm: Dict[str, LLMModelInfo] = Field(default_factory=dict)
    embeddings: Dict[str, LLMModelInfo] = Field(default_factory=dict)
    decision: Dict[str, LLMModelInfo] = Field(default_factory=dict)
    stt: Dict[str, SttModelInfo] = Field(default_factory=dict)
    tts: Dict[str, TtsModelInfo] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    source: Optional[str] = None

    # ------------------------------------------------------------- prices
    def llm_pricing(self) -> Dict[str, ModelPrice]:
        """USD per million tokens for every model that has a price (chat, embedding, decision)."""
        out: Dict[str, ModelPrice] = {}
        for group in (self.embeddings, self.decision, self.llm):
            for name, m in group.items():
                if m.price is not None:
                    out[name] = m.price
        return out

    def stt_price(self, name: Optional[str]) -> Optional[float]:
        m = self.stt.get(name or "")
        return m.per_minute_cost if m else None

    def tts_price(self, name: Optional[str]) -> Optional[float]:
        m = self.tts.get(name or "")
        return m.per_1k_chars_cost if m else None

    def find_llm(self, name: Optional[str]) -> Optional[LLMModelInfo]:
        return self.llm.get(name or "") or self.embeddings.get(name or "") or self.decision.get(name or "")


def _section(raw: Mapping[str, Any], *path: str) -> Any:
    node: Any = raw
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _build(kind: str, items: Any, factory, problems: List[str], warnings: List[str], *, fixups=None) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if items is None:
        return out
    if not isinstance(items, list):
        problems.append(f"{kind}: expected a list of models, got {type(items).__name__}")
        return out
    for i, item in enumerate(items):
        where = f"{kind}[{i}]" + (f" ({item.get('name')})" if isinstance(item, dict) and item.get("name") else "")
        if not isinstance(item, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        try:
            data = dict(item)
            for key in ("context_length", "max_output_length"):
                if key in data:
                    data[key] = parse_size(data[key])
            for key in ("tool_use", "reasoning", "vision"):
                if key in data:
                    data[key] = _flag(data[key])
            if fixups:
                fixups(data)
            info = factory.model_validate({k: v for k, v in data.items() if k in factory.model_fields})
        except (ValueError, ValidationError) as exc:
            problems.append(f"{where}: {str(exc).splitlines()[0] if isinstance(exc, ValueError) and not isinstance(exc, ValidationError) else _first_error(exc)}")
            continue
        if info.name in out:
            warnings.append(f"{kind}: {info.name!r} is listed twice; the later entry is used")
        out[info.name] = info
    return out


def _first_error(exc: ValidationError) -> str:
    e = exc.errors()[0]
    return f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"


def parse_registry(raw: Any, *, source: Optional[str] = None) -> ModelRegistry:
    """Build a registry from the parsed YAML. Raises ModelRegistryError listing EVERY problem."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ModelRegistryError(f"{source or 'models'}: the top level must be a mapping")
    problems: List[str] = []
    warnings: List[str] = []

    def tts_fix(d: Dict[str, Any]) -> None:
        # a registry written before the keys were renamed: per_minute_cost was really the price per 1K characters
        if "per_1k_chars_cost" not in d and "per_1m_chars_cost" in d:
            d["per_1k_chars_cost"] = float(d["per_1m_chars_cost"]) / 1000.0

    llm_node = raw.get("llm")
    llm_items = llm_node if isinstance(llm_node, list) else _section(raw, "llm", "chat")        # flat list, or the older llm.chat
    emb_items = raw.get("embeddings") if "embeddings" in raw else _section(raw, "models", "embeddings")
    reg = ModelRegistry(
        llm=_build("llm", llm_items, LLMModelInfo, problems, warnings),
        embeddings=_build("embeddings", emb_items, LLMModelInfo, problems, warnings),
        decision=_build("decision", raw.get("decision"), LLMModelInfo, problems, warnings),
        stt=_build("speech.stt", _section(raw, "speech", "stt"), SttModelInfo, problems, warnings),
        tts=_build("speech.tts", _section(raw, "speech", "tts"), TtsModelInfo, problems, warnings, fixups=tts_fix),
        source=source,
    )
    if _section(raw, "speech", "llm") is not None:
        warnings.append("`llm` is nested under `speech`: move it to the top level (it is ignored there)")
    if problems:
        raise ModelRegistryError(f"{source or 'models'}: {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    reg.warnings = warnings
    return reg


def load_registry(path: str | Path) -> ModelRegistry:
    """Read models.yaml. A missing file gives an EMPTY registry (with a warning), so a deployment without one still
    starts; a malformed file raises ModelRegistryError (a typo in a price must not be silently ignored)."""
    p = Path(path)
    if not p.is_file():
        return ModelRegistry(warnings=[f"{p} not found: no model prices (costs will use what the provider reports)"], source=str(p))
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ModelRegistryError(f"{p}: cannot read: {exc}") from exc
    return parse_registry(raw, source=str(p))


# ───────────────────────────────────────────────────────── checks for the configured models

#: roles whose model must call tools: the planner emits a JSON plan, agents act through tools
NEEDS_TOOLS = ("planner", "agent")


def check_configured(registry: ModelRegistry, roles: Mapping[str, str], *, stt: Optional[str] = None,
                     tts: Optional[str] = None) -> List[str]:
    """Human-readable problems with the models the config selects. Empty list = all good."""
    out: List[str] = []
    if not registry.llm and not registry.stt and not registry.tts:
        return out                                     # no registry loaded: nothing to compare with
    for role, model in roles.items():
        info = registry.find_llm(model)
        if info is None:
            out.append(f"{role}: model {model!r} is not in models.yaml, so its cost is only known if the provider reports it")
            continue
        if role in NEEDS_TOOLS and info.tool_use is False:
            out.append(f"{role}: model {model!r} has tool_use disabled in models.yaml, but the {role} role needs tool/JSON action support")
        if info.price is None:
            out.append(f"{role}: model {model!r} has no price in models.yaml")
    for kind, name, getter in (("stt", stt, registry.stt_price), ("tts", tts, registry.tts_price)):
        if name and getter(name) is None:
            out.append(f"{kind}: model {name!r} has no price in models.yaml")
    return out


# ───────────────────────────────────────────────────────── report for the admin panel

def registry_report(registry: ModelRegistry, roles: Mapping[str, str]) -> Dict[str, Any]:
    """Everything the admin "Models & prices" view shows: the configured roles with what the registry knows about
    them, every model that can be chosen (cheapest first), and the problems found."""
    def llm_row(m: LLMModelInfo) -> Dict[str, Any]:
        return {"name": m.name, "provider": m.provider, "tool_use": m.tool_use, "reasoning": m.reasoning, "vision": m.vision,
                "context_length": m.context_length, "max_output_length": m.max_output_length,
                "input_per_million": m.input_per_million, "output_per_million": m.output_per_million,
                "cache_read_per_million": m.cache_read_per_million}

    configured: List[Dict[str, Any]] = []
    for role, model in roles.items():
        if not model:
            continue
        row: Dict[str, Any] = {"role": role, "model": model, "known": False}
        if role == "stt":
            m = registry.stt.get(model)
            row.update(kind="stt", known=m is not None, per_minute_cost=m.per_minute_cost if m else None)
        elif role == "tts":
            m = registry.tts.get(model)
            row.update(kind="tts", known=m is not None, per_1k_chars_cost=m.per_1k_chars_cost if m else None)
        else:
            m = registry.find_llm(model)
            row.update(kind="llm", known=m is not None, **({k: v for k, v in llm_row(m).items() if k != "name"} if m else {}))
        configured.append(row)
    price_key = lambda m: (m.input_per_million is None, (m.input_per_million or 0) + (m.output_per_million or 0), m.name)
    stt_sel, tts_sel = roles.get("stt"), roles.get("tts")
    return {
        "source": registry.source,
        "roles": configured,
        "llm": [llm_row(m) for m in sorted(registry.llm.values(), key=price_key)],
        "stt": [{"name": m.name, "provider": m.provider, "per_minute_cost": m.per_minute_cost} for m in registry.stt.values()],
        "tts": [{"name": m.name, "provider": m.provider, "per_1k_chars_cost": m.per_1k_chars_cost} for m in registry.tts.values()],
        "warnings": [*registry.warnings, *check_configured(registry, {r: m for r, m in roles.items() if r not in ("stt", "tts") and m},
                                                              stt=stt_sel, tts=tts_sel)],
    }
