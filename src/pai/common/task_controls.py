"""What a user may do to a task once it is running — declared by the CAPABILITY, not the task type.

A plugin knows whether its action can be paused, and how a cancel behaves; a "one-shot" or
"long-running" label says nothing about that. So the manifest (or, for built-ins, the
capability definition) declares it, the task carries it, and the API/UI only offer what is
real. Never read from LLM or plan output.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CancelMode(StrEnum):
    """How cancelling behaves AFTER the action has been handed to the device/plugin."""
    COOPERATIVE = "cooperative"    # the action checks for cancellation and stops promptly
    BEST_EFFORT = "best_effort"    # we stop waiting and ask it to stop; it may still finish (default)
    NONE = "none"                  # cannot be cancelled once started (e.g. an atomic payment)


class CancelOutcomeKind(StrEnum):
    """What a cancel actually achieved (reported on the task)."""
    NOT_STARTED = "not_started"    # it had not reached the device: nothing ran
    ACKED = "acked"                # the executor/device confirmed it stopped
    ABANDONED = "abandoned"        # we stopped waiting; the device may have completed it


class TaskControls(BaseModel):
    """Per-capability control support. Defaults are the safe, honest ones."""
    model_config = ConfigDict(frozen=True)

    # True only if the plugin can really pause and ACKNOWLEDGE it (e.g. a recording, a stream).
    pause: bool = False
    cancel: CancelMode = CancelMode.BEST_EFFORT
