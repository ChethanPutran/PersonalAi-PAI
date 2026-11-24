"""LLM token and cost accounting.

Every provider reports token usage through one choke point (`LLMProvider._trace_llm`); this module turns
that into money and keeps the running totals:

* price per million tokens comes from config (`llm_pricing[<model>]`, else the provider's own
  `cost_per_million_input/output`); if none is configured, the cost the gateway itself reported
  (AIRouter `usage.total_cost`, OpenRouter `usage.cost`) is used; with neither the cost is unknown
  (`None`, never a made-up zero) while tokens are still counted;
* `CostTracker` keeps totals overall and per provider / model / route (planner, chat, agent ...) and,
  optionally, appends every call to a JSONL ledger so totals survive restarts.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, Mapping, Optional

from loguru import logger

from pai.config.models.llm import ModelPrice

# Which role (route) the current call belongs to; set by LLMRouter, read when the usage is recorded.
current_route: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_route", default=None)

COST_CONFIGURED = "configured"      # tokens x the price per million set in config
COST_PROVIDER = "provider"          # the cost the gateway reported for the call


def compute_cost(prompt_tokens: int, completion_tokens: int, price: ModelPrice) -> float:
    """USD for one call: tokens / 1e6 x price per million, input and output priced separately."""
    return (max(0, prompt_tokens) * price.input_per_million
            + max(0, completion_tokens) * price.output_per_million) / 1_000_000


def as_cost(value: Any) -> Optional[float]:
    """A gateway-reported cost (number or numeric string) -> float, else None. Never raises."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 and f == f else None          # rejects negatives and NaN


class PriceBook:
    """Resolves the price of a model: per-model entry first, then the provider-wide default."""

    def __init__(self, per_model: Optional[Mapping[str, ModelPrice]] = None,
                 default: Optional[ModelPrice] = None) -> None:
        self._per_model = dict(per_model or {})
        self._default = default

    def price_for(self, model: Any) -> Optional[ModelPrice]:
        return self._per_model.get(str(getattr(model, "value", model))) or self._default


@dataclass
class UsageRecord:
    ts: float
    provider: str
    model: str
    route: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Optional[float]
    cost_source: Optional[str]
    breakdown: Dict[str, int] = field(default_factory=dict)     # prompt tokens per part (estimated split)
    trace_id: Optional[str] = None                                # the goal/request this call belonged to
    units: Optional[float] = None                                 # metered quantity for non-token usage (voice)
    unit: Optional[str] = None                                    # "seconds" of audio / "characters" of text

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class _Totals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0          # calls whose cost was unknown (no price configured, none reported)

    def add(self, r: UsageRecord) -> None:
        self.calls += 1
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.total_tokens += r.total_tokens
        if r.cost_usd is None:
            self.unpriced_calls += 1
        else:
            self.cost_usd += r.cost_usd

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["cost_usd"] = round(self.cost_usd, 8)
        return d


@dataclass
class CostTracker:
    """Running token/cost totals. Safe to call from any thread (a lock guards the counters).

    Credits: the operator gives a starting balance in USD (`initial_credits_usd` from config, or set at
    runtime from the admin panel and saved in `credits_path`). Remaining = balance - the known cost of calls
    made since it was set. A balance set from the admin panel counts from the moment it was set (it
    replaces the config value until cleared); the config value counts all tracked spend. Calls with an
    unknown price cost nothing here (they are counted in `unpriced_calls`). Credits are informational:
    nothing is blocked when they run out.
    """

    ledger_path: Optional[Path] = None
    initial_credits_usd: Optional[float] = None
    credits_path: Optional[Path] = None
    recent_size: int = 200
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _all: _Totals = field(default_factory=_Totals, repr=False)
    _by: Dict[str, Dict[str, _Totals]] = field(default_factory=lambda: {"provider": {}, "model": {}, "route": {}},
                                               repr=False)
    _sections: Dict[str, Dict[str, int]] = field(default_factory=dict, repr=False)   # name -> {tokens, calls}
    # (minute, route, model) -> totals, the source for time series (hour/day buckets are rolled up from it)
    _minutes: Dict[Any, _Totals] = field(default_factory=dict, repr=False)
    # route -> {unit, units, calls} for metered (non-token) usage such as voice
    _units: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)
    _recent: Deque[UsageRecord] = field(default_factory=deque, repr=False)
    _since: float = field(default_factory=time.time)
    _ledger_failed: bool = False
    # the active credit baseline: {source, balance_usd, as_of} or None; spend since it was set
    _credit: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _credit_spent: float = field(default=0.0, repr=False)
    _credit_unpriced: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self._recent = deque(maxlen=self.recent_size)
        self._init_credit()
        if self.ledger_path is not None:
            self.ledger_path = Path(self.ledger_path)
            self._load_ledger()

    # ------------------------------------------------------------------ record

    def record(self, *, provider: Any, model: Any, prompt_tokens: int, completion_tokens: int,
               total_tokens: int, cost_usd: Optional[float], cost_source: Optional[str],
               route: Optional[str] = None, breakdown: Optional[Dict[str, int]] = None,
               trace_id: Optional[str] = None, ts: Optional[float] = None,
               units: Optional[float] = None, unit: Optional[str] = None) -> UsageRecord:
        r = UsageRecord(
            ts=ts if ts is not None else time.time(), provider=str(getattr(provider, "value", provider)),
            model=str(getattr(model, "value", model)), route=route or current_route.get() or "default",
            prompt_tokens=int(prompt_tokens), completion_tokens=int(completion_tokens),
            # same rule as LLMUsage: a missing/zero total means prompt + completion
            total_tokens=int(total_tokens) if int(total_tokens) > 0 else int(prompt_tokens) + int(completion_tokens),
            cost_usd=cost_usd, cost_source=cost_source,
            breakdown={str(k): int(v) for k, v in (breakdown or {}).items() if v}, trace_id=trace_id,
            units=None if units is None else float(units), unit=unit)
        with self._lock:
            self._add(r)
            self._recent.append(r)
        self._append_ledger(r)
        return r

    def _add(self, r: UsageRecord) -> None:
        self._all.add(r)
        if self._credit is not None and r.ts >= self._credit["as_of"]:
            if r.cost_usd is None:
                self._credit_unpriced += 1
            else:
                self._credit_spent += r.cost_usd
        for dim, key in (("provider", r.provider), ("model", r.model), ("route", r.route)):
            self._by[dim].setdefault(key, _Totals()).add(r)
        self._minutes.setdefault((int(r.ts // 60) * 60, r.route, r.model), _Totals()).add(r)
        if r.unit:
            u = self._units.setdefault(r.route, {"unit": r.unit, "units": 0.0, "calls": 0})
            u["units"] += r.units or 0.0
            u["calls"] += 1
        for name, tokens in r.breakdown.items():
            sec = self._sections.setdefault(name, {"tokens": 0, "calls": 0})
            sec["tokens"] += tokens
            sec["calls"] += 1

    # ----------------------------------------------------------------- credits

    def _init_credit(self) -> None:
        """Baseline = the admin-saved balance if there is one, else the configured initial credits."""
        saved = self._read_credit_file()
        if saved is not None:
            self._credit = saved
        elif self.initial_credits_usd is not None:
            self._credit = {"source": "config", "balance_usd": float(self.initial_credits_usd), "as_of": 0.0}

    def _read_credit_file(self) -> Optional[Dict[str, Any]]:
        if self.credits_path is None:
            return None
        try:
            d = json.loads(Path(self.credits_path).read_text(encoding="utf-8"))
            balance, as_of = float(d["balance_usd"]), float(d["as_of"])
            if balance < 0 or balance != balance:
                return None
            return {"source": "admin", "balance_usd": balance, "as_of": as_of}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def set_initial_credits(self, value: Optional[float]) -> None:
        """Settings reload: a new `llm_cost.initial_credits_usd`. A balance set from the admin panel wins and is left
        alone; otherwise the configured credits are re-applied against all tracked spend."""
        with self._lock:
            self.initial_credits_usd = None if value is None else float(value)
            if self._credit is not None and self._credit["source"] == "admin":
                return
            self._credit = (None if value is None else {"source": "config", "balance_usd": float(value), "as_of": 0.0})
            self._credit_spent = self._all.cost_usd if self._credit is not None else 0.0
            self._credit_unpriced = self._all.unpriced_calls if self._credit is not None else 0

    def set_credits(self, balance_usd: float, *, now: Optional[float] = None) -> Dict[str, Any]:
        """The operator says: I have this many USD of credits right now. Counting restarts from now."""
        balance = float(balance_usd)
        if not (balance >= 0) or balance == float("inf"):
            raise ValueError("credits must be a number >= 0")
        credit = {"source": "admin", "balance_usd": balance, "as_of": now if now is not None else time.time()}
        with self._lock:
            self._credit, self._credit_spent, self._credit_unpriced = credit, 0.0, 0
        if self.credits_path is not None:
            try:
                path = Path(self.credits_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"balance_usd": balance, "as_of": credit["as_of"]}), encoding="utf-8")
            except OSError as exc:
                logger.warning("LLM credits file {} is not writable ({}); the balance lasts until restart",
                               self.credits_path, exc)
        return self.credits_summary()

    def clear_credits(self) -> Dict[str, Any]:
        """Forget the admin-set balance and go back to the configured initial credits (if any)."""
        if self.credits_path is not None:
            try:
                Path(self.credits_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("could not remove {}: {}", self.credits_path, exc)
        with self._lock:
            self._credit, self._credit_spent, self._credit_unpriced = None, 0.0, 0
            if self.initial_credits_usd is not None:
                self._credit = {"source": "config", "balance_usd": float(self.initial_credits_usd), "as_of": 0.0}
        if self._credit is not None and self.ledger_path is not None:
            self._replay_credit_from_ledger()
        return self.credits_summary()

    def _replay_credit_from_ledger(self) -> None:
        spent, unpriced = 0.0, 0
        try:
            for line in Path(self.ledger_path).read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    if float(d["ts"]) < self._credit["as_of"]:
                        continue
                    if d.get("cost_usd") is None:
                        unpriced += 1
                    else:
                        spent += float(d["cost_usd"])
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            pass
        with self._lock:
            self._credit_spent, self._credit_unpriced = spent, unpriced

    def credits_summary(self) -> Dict[str, Any]:
        with self._lock:
            c = self._credit
            if c is None:
                return {"configured": False, "source": None, "initial_usd": None, "spent_usd": 0.0,
                        "remaining_usd": None, "used_pct": None, "exhausted": False, "as_of": None,
                        "unpriced_calls": 0}
            spent = round(self._credit_spent, 8)
            remaining = round(c["balance_usd"] - self._credit_spent, 8)
            pct = None if c["balance_usd"] <= 0 else round(min(100.0, 100.0 * self._credit_spent / c["balance_usd"]), 2)
            return {"configured": True, "source": c["source"], "initial_usd": c["balance_usd"],
                    "spent_usd": spent, "remaining_usd": remaining, "used_pct": pct,
                    "exhausted": remaining <= 0, "as_of": c["as_of"] or None,
                    "unpriced_calls": self._credit_unpriced}

    def record_metered(self, *, provider: Any, model: Any, route: str, units: float, unit: str,
                       cost_usd: Optional[float], ts: Optional[float] = None) -> UsageRecord:
        """Usage that is not tokens: seconds of audio transcribed, characters spoken. Counted as one call with
        0 tokens; its cost is deducted from the credits like any other."""
        return self.record(provider=provider, model=model, prompt_tokens=0, completion_tokens=0, total_tokens=0,
                           cost_usd=cost_usd, cost_source="configured" if cost_usd is not None else None,
                           route=route, units=units, unit=unit, ts=ts)

    # ----------------------------------------------------------------- time series

    BUCKETS = {"minute": 60, "hour": 3600, "day": 86400}

    def series(self, *, bucket: str = "hour", since: Optional[float] = None, until: Optional[float] = None,
               route: Optional[str] = None, model: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
        """Calls, tokens and cost per time bucket (oldest first), for charts.

        Empty buckets inside the window are included (zeros) so a chart's x-axis has no gaps. Default window:
        the last 24 buckets. `cost_usd` counts priced calls only (`unpriced_calls` says how many were not).
        Hour/day buckets are aligned to UTC."""
        if bucket not in self.BUCKETS:
            raise ValueError(f"bucket must be one of {sorted(self.BUCKETS)}")
        size = self.BUCKETS[bucket]
        end = int((until if until is not None else (now if now is not None else time.time())) // size) * size
        start = int(since // size) * size if since is not None else end - 23 * size
        if start > end:
            start = end
        if (end - start) // size > 2000:
            raise ValueError("window too large for this bucket size")
        points: Dict[int, _Totals] = {t: _Totals() for t in range(start, end + size, size)}
        with self._lock:
            for (minute, r, m), tot in self._minutes.items():
                if (route and r != route) or (model and m != model):
                    continue
                b = int(minute // size) * size
                if b in points:
                    p = points[b]
                    p.calls += tot.calls
                    p.prompt_tokens += tot.prompt_tokens
                    p.completion_tokens += tot.completion_tokens
                    p.total_tokens += tot.total_tokens
                    p.cost_usd += tot.cost_usd
                    p.unpriced_calls += tot.unpriced_calls
        return {"bucket": bucket, "bucket_seconds": size, "start": start, "end": end + size,
                "points": [{"t": t, **points[t].to_dict()} for t in sorted(points)]}

    VOICE_ROUTES = ("voice.stt", "voice.tts")

    def voice_summary(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Voice cost at a glance: audio transcribed (seconds) and text spoken (characters), each with its cost,
        overall and for the last hour, plus the combined cost. Priced calls only count toward `cost_usd`."""
        now = now if now is not None else time.time()
        out: Dict[str, Any] = {}
        total_cost = total_hour = 0.0
        with self._lock:
            for route in self.VOICE_ROUTES:
                tot = self._by["route"].get(route)
                u = self._units.get(route, {})
                hour = sum(t.cost_usd for (minute, r, _m), t in self._minutes.items() if r == route and minute >= now - 3600)
                hour_calls = sum(t.calls for (minute, r, _m), t in self._minutes.items() if r == route and minute >= now - 3600)
                cost = tot.cost_usd if tot else 0.0
                out[route.split(".")[1]] = {
                    "calls": tot.calls if tot else 0, "unit": u.get("unit"), "units": round(u.get("units", 0.0), 3),
                    "cost_usd": round(cost, 8), "unpriced_calls": tot.unpriced_calls if tot else 0,
                    "last_hour_calls": hour_calls, "last_hour_cost_usd": round(hour, 8)}
                total_cost += cost
                total_hour += hour
        out["total_cost_usd"] = round(total_cost, 8)
        out["last_hour_cost_usd"] = round(total_hour, 8)
        return out

    # ----------------------------------------------------------------- reading

    def summary(self, *, recent: int = 20) -> Dict[str, Any]:
        credits = self.credits_summary()
        with self._lock:
            return {
                "since": self._since,
                "credits": credits,
                "total": self._all.to_dict(),
                "by_provider": {k: v.to_dict() for k, v in self._by["provider"].items()},
                "by_model": {k: v.to_dict() for k, v in self._by["model"].items()},
                "by_route": {k: v.to_dict() for k, v in self._by["route"].items()},
                # Estimated split of PROMPT tokens by part (system_prompt, capabilities, history, memory,
                # user_prompt, tool_data). Proportions are approximate; each call's parts sum to its real total.
                "by_section": {k: dict(v) for k, v in self._sections.items()},
                "by_unit": {k: {**v, "units": round(v["units"], 3)} for k, v in self._units.items()},
                "recent": [r.to_dict() for r in list(self._recent)[-max(0, recent):]],
            }

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return self._all.cost_usd

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return self._all.total_tokens

    # ------------------------------------------------------------------ ledger

    def _append_ledger(self, r: UsageRecord) -> None:
        if self.ledger_path is None or self._ledger_failed:
            return
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.ledger_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r.to_dict(), separators=(",", ":")) + "\n")
        except OSError as exc:                       # accounting must never break an LLM call
            self._ledger_failed = True
            logger.warning("LLM usage ledger {} is not writable ({}); totals stay in memory only",
                           self.ledger_path, exc)

    def _load_ledger(self) -> None:
        """Rebuild the totals from the ledger. Unreadable lines are skipped, not fatal."""
        try:
            lines: Iterator[str] = iter(self.ledger_path.read_text(encoding="utf-8").splitlines())
        except (OSError, FileNotFoundError):
            return
        loaded = 0
        for line in lines:
            try:
                d = json.loads(line)
                optional = ("breakdown", "trace_id", "units", "unit")
                r = UsageRecord(**{k: d[k] for k in UsageRecord.__dataclass_fields__ if k not in optional},
                                breakdown=d.get("breakdown") or {}, trace_id=d.get("trace_id"),
                                units=d.get("units"), unit=d.get("unit"))
            except (ValueError, KeyError, TypeError):
                continue
            self._add(r)
            self._recent.append(r)
            loaded += 1
            self._since = min(self._since, r.ts)
        if loaded:
            logger.info("LLM usage: restored {} call(s) from {} ({} tokens, ${:.4f})",
                        loaded, self.ledger_path, self._all.total_tokens, self._all.cost_usd)
