"""Reload the saved settings into the RUNNING server, without a restart and without touching the admin session.

A full in-process rebuild would drop every connected device and interrupt running tasks, so this reloads what can be
swapped safely and says plainly what cannot:

  applied live            llm_provider, llm_routes, llm_pricing, models_file (+ the prices in models.yaml)  -> LLMRouter
                          voice (providers, models, prices, limits)                                         -> VoiceService
                          llm_cost.initial_credits_usd                                                      -> CostTracker
                          admin.history_retention_days                                                      -> AdminHistory
  needs a restart         everything else (ports, database, storage, security, executors, ...)

Nothing is half-applied: the new configuration is loaded and validated first, each live group either applies or reports
why it did not, and `running_settings` only moves for what really changed.
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from loguru import logger

from pai.config import settings_admin as S

# path prefixes that can be applied to a running server, and the group that applies them
LIVE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "llm": ("llm_provider", "llm_routes", "llm_pricing", "models_file"),
    "voice": ("voice",),
    "credits": ("llm_cost.initial_credits_usd",),
    "history": ("admin.history_retention_days",),
}


def _group_of(path: str) -> Optional[str]:
    for group, prefixes in LIVE_GROUPS.items():
        if any(path == p or path.startswith(p + ".") for p in prefixes):
            return group
    return None


def diff_settings(running: Mapping[str, Any], new: Mapping[str, Any]) -> List[str]:
    """Leaf setting paths whose value differs between two (secret-free) config dumps."""
    a, b = S.flatten(running), S.flatten(new)
    return sorted(p for p in set(a) | set(b) if a.get(p, None) != b.get(p, None))


# ─────────────────────────────────────────────────────── the live groups

def _fill_voice_prices(config: Any, registry: Any) -> Any:
    """Voice prices that are null in the config come from models.yaml (the same rule as at startup)."""
    air = config.voice.airouter
    if air.stt_cost_per_minute_usd is None:
        air.stt_cost_per_minute_usd = registry.stt_price(air.stt_model)
    if air.tts_cost_per_1k_chars_usd is None:
        air.tts_cost_per_1k_chars_usd = registry.tts_price(air.tts_model)
    return air


def _reload_llm(context: Any, config: Any) -> str:
    from pai.config.model_registry import check_configured, load_registry
    registry = load_registry(config.models_file)
    pricing = {**registry.llm_pricing(), **config.llm_pricing}
    router = context.orchestrator.planner.llm
    router.reconfigure(config.llm_provider, routes=dict(config.llm_routes), pricing=pricing)
    air = _fill_voice_prices(config, registry)
    roles = {"default": str(config.llm_provider.model_name), **{r: str(m) for r, m in config.llm_routes.items()}}
    context.model_registry = registry
    context.model_roles = {**roles, "stt": air.stt_model or "", "tts": air.tts_model or ""}
    for note in [*registry.warnings, *check_configured(registry, roles, stt=air.stt_model, tts=air.tts_model)]:
        logger.warning("models registry: {}", note)
    return f"model '{config.llm_provider.model_name}', {len(config.llm_routes)} route(s), {len(pricing)} priced model(s)"


def _reload_voice(context: Any, config: Any) -> str:
    voice = getattr(context, "voice", None)
    if voice is None:
        return "voice is not configured"
    from pai.config.model_registry import load_registry
    _fill_voice_prices(config, load_registry(config.models_file))
    tracker = getattr(context.orchestrator.planner.llm, "cost_tracker", None)
    voice.reload(config.voice, cost_tracker=tracker)
    return "voice available" if voice.available else f"voice unavailable: {voice.unavailable_reason}"


def _reload_credits(context: Any, config: Any) -> str:
    tracker = getattr(context.orchestrator.planner.llm, "cost_tracker", None)
    if tracker is None:
        return "cost tracking is off"
    tracker.set_initial_credits(config.llm_cost.initial_credits_usd)
    return "credits updated"


def _reload_history(context: Any, config: Any) -> str:
    history = getattr(getattr(context, "admin", None), "history", None)
    if history is None:
        return "admin history is off"
    history.retention_days = max(1, int(config.admin.history_retention_days))
    return f"keeping {history.retention_days} day(s)"


_RELOADERS: Dict[str, Callable[[Any, Any], str]] = {
    "llm": _reload_llm, "voice": _reload_voice, "credits": _reload_credits, "history": _reload_history,
}


# ─────────────────────────────────────────────────────── the entry point

async def reload_settings(context: Any, load_config: Callable[[], Any]) -> Dict[str, Any]:
    """Load the saved configuration and apply what can be applied live. Never raises for a bad configuration: the
    report says `ok: false` and nothing has been changed."""
    try:
        new_config = await asyncio.to_thread(load_config)
    except Exception as exc:                                   # a typo in overrides.yaml must not take the server down
        return {"ok": False, "error": f"the saved settings could not be loaded: {str(exc).splitlines()[0][:300]}",
                "applied": [], "restart_required": [], "failed": []}

    new_dump = S.strip_secrets(new_config.model_dump(mode="json"))
    running = S.strip_secrets(context.running_settings)
    changed = diff_settings(running, new_dump)
    live: Dict[str, List[str]] = {}
    cold: List[str] = []
    for path in changed:
        group = _group_of(path)
        (live.setdefault(group, []) if group else cold).append(path)

    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    new_running = copy.deepcopy(context.running_settings)
    for group in (g for g in LIVE_GROUPS if g in live):         # fixed order: llm before voice, credits, history
        paths = live[group]
        try:
            detail = _RELOADERS[group](context, new_config)
        except Exception as exc:
            logger.exception("settings reload: {} failed", group)
            failed.append({"group": group, "paths": paths, "error": str(exc)[:300]})
            continue
        applied.append({"group": group, "paths": paths, "detail": detail})
        for p in paths:                                         # the running config now really has this value
            S.set_path(new_running, p, S.get_path(new_dump, p))
    context.running_settings = new_running
    logger.info("settings reload: applied {} group(s), {} need a restart, {} failed",
                len(applied), len(cold), len(failed))
    return {"ok": not failed, "applied": applied, "failed": failed,
            "restart_required": cold, "unchanged": not changed}
