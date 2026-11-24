"""Ordered, bounded, never-raising shutdown with a final report."""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, List

from loguru import logger
from pydantic import BaseModel, Field


class StepResult(BaseModel):
    name: str
    ok: bool
    seconds: float
    error: str = ""


class ShutdownReport(BaseModel):
    steps: List[StepResult] = Field(default_factory=list)
    leftover_tasks: List[str] = Field(default_factory=list)   # asyncio tasks still alive afterwards

    @property
    def clean(self) -> bool:
        return all(s.ok for s in self.steps) and not self.leftover_tasks

    def summary(self) -> str:
        bad = [f"{s.name}({s.error or 'failed'})" for s in self.steps if not s.ok]
        total = sum(s.seconds for s in self.steps)
        text = f"{len(self.steps)} steps in {total:.1f}s"
        if bad:
            text += f"; DID NOT STOP CLEANLY: {', '.join(bad)}"
        if self.leftover_tasks:
            text += f"; still running: {', '.join(self.leftover_tasks[:10])}"
        return text


async def run_step(
    report: ShutdownReport, name: str, step: Callable[[], Awaitable[object]], *, timeout: float,
) -> None:
    """Run one shutdown step: bounded by ``timeout``; errors are recorded, never raised."""
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(step(), timeout=timeout)
        report.steps.append(StepResult(name=name, ok=True, seconds=time.perf_counter() - t0))
    except asyncio.TimeoutError:
        logger.error("shutdown step '{}' timed out after {}s", name, timeout)
        report.steps.append(StepResult(name=name, ok=False, seconds=time.perf_counter() - t0,
                                       error=f"timeout {timeout:g}s"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("shutdown step '{}' failed: {}: {}", name, type(exc).__name__, exc)
        report.steps.append(StepResult(name=name, ok=False, seconds=time.perf_counter() - t0,
                                       error=f"{type(exc).__name__}: {exc}"))


def leftover_task_names(ignore: "asyncio.Task | None" = None) -> List[str]:
    me = ignore or asyncio.current_task()
    return sorted(t.get_name() for t in asyncio.all_tasks() if t is not me and not t.done())
