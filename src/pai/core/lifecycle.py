"""
Uniform lifecycle contract for every long-lived component in PAI.

The protocol is intentionally minimal:

    name           : str        — identifier used in logs and error messages
    initialize     : async      — idempotent
    shutdown       : async      — idempotent
    is_initialized : sync → bool — cheap, no I/O

Why `is_initialized` is sync, not async:
  - The `Manager` base already implements it as a sync property accessor,
    and the vast majority of call sites just need to read a flag.
  - Any component that *would* need to probe a remote (e.g. an API
    health check) should implement that separately as `health_check()`,
    not overload the lifecycle contract with an I/O-bearing predicate.

Conforming today:
    Manager                       (base class — inherited by every manager)
    CapabilityRouter              (after migration in the previous answer)
    EventBus                      (add the three methods, see below)
    TaskRunner                    (inherit Manager)

Non-conforming (deliberately):
    EventStore / TraceStore       — their start/stop are async but their
                                    "is started" state isn't queried
    Anything short-lived          — request-scoped objects (ExecutionContext)
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from loguru import logger

from abc import ABC, abstractmethod

class Lifecycle(ABC):
    """Components that participate in the application's startup/shutdown."""

    name: str = "component"

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    @abstractmethod
    def is_initialized(self) -> bool: ...

# ---------------------------------------------------------------------------
# Single-component assertions
# ---------------------------------------------------------------------------

def require_initialized(
    **components: Optional[Lifecycle],
) -> None:
    """
    Raise if any of `components` is present but not initialized.

    `None` values are skipped — useful for optional dependencies like
    `event_bus`. All failing components are collected into one message,
    so the operator fixes the whole wiring in one restart rather than
    discovering them one at a time.

    Example:
        require_initialized(
            capability_resolver=self.capability_resolver,
            device_selector=self.device_selector,
            authorizer=self.authorizer,
            executor_manager=self.executor_manager,
            task_manager=self.task_manager,
            event_bus=self.event_bus,          # may be None — skipped
        )
    """
    bad: list[str] = []
    for label, component in components.items():
        if component is None:
            continue
        try:
            ready = component.is_initialized()
        except Exception as exc:
            bad.append(f"{label} (error: {type(exc).__name__}: {exc})")
            continue
        if not ready:
            bad.append(label)

    if bad:
        raise RuntimeError(
            "Components not initialized: "
            + ", ".join(bad)
            + ". Initialize them before using this service."
        )


def require_conforms(**components: Optional[Any]) -> None:
    """
    Raise if any of `components` is present but doesn't implement
    the Lifecycle protocol at all (missing methods).

    Run this at startup, before `initialize()` — it catches the typo /
    wrong-type bug early, with a clear message.
    """
    bad: list[str] = []
    for label, component in components.items():
        if component is None:
            continue
        if not isinstance(component, Lifecycle):
            missing = [
                m for m in ("initialize", "shutdown", "is_initialized")
                if not hasattr(component, m)
            ]
            bad.append(
                f"{label} ({type(component).__name__} missing {missing})"
            )
    if bad:
        raise RuntimeError(
            "Components do not implement Lifecycle: " + ", ".join(bad)
        )


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

async def initialize_all(
    timeout: float = 10.0,
    **components: Optional[Lifecycle],
) -> None:
    """
    Initialize all non-None components in **declaration order**.

    Order matters: pass dependencies before dependents
    (plugin_manager before capability_router, etc.).

    A timeout raises — unlike `ExecutorManager.initialize_all` which
    tolerates it. That's a deliberate difference: missing an executor
    only degrades some capabilities, whereas missing a router or a
    manager leaves the app in a broken state. If you want the tolerant
    behaviour, use ExecutorManager's own method.

    Stops at the first failure. Components initialized earlier stay up,
    so call `shutdown_all(**same_components)` from the lifespan's
    try/finally.
    """
    for label, component in components.items():
        if component is None:
            continue
        try:
            await asyncio.wait_for(component.initialize(), timeout=timeout)
            logger.debug("Initialized {}", label)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Component '{label}' initialization timed out "
                f"after {timeout}s"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Component '{label}' initialization failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc


async def shutdown_all(
    timeout: float = 5.0,
    **components: Optional[Lifecycle],
) -> Dict[str, str]:
    """
    Shut down all non-None components in **declaration order**.

    Callers should pass components in *reverse* initialization order so
    dependents shut down before their dependencies.

    Never raises — failures are collected and returned so the caller can
    log them but continue teardown of the remaining components.
    """
    failures: Dict[str, str] = {}
    for label, component in components.items():
        if component is None:
            continue
        try:
            await asyncio.wait_for(component.shutdown(), timeout=timeout)
            logger.debug("Shut down {}", label)
        except asyncio.TimeoutError:
            failures[label] = f"timeout after {timeout}s"
            logger.warning("Shutdown of '{}' timed out", label)
        except Exception as exc:
            failures[label] = f"{type(exc).__name__}: {exc}"
            logger.exception("Shutdown of '{}' failed", label)
    return failures