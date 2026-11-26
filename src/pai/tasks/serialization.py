"""Task -> JSON dict, the one shape every client sees (REST lists, details, SSE events)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from pai.common.task_controls import TaskControls
from pai.storage.repositories.task import upgrade_legacy_payload
from pai.tasks.models import Task, TaskControlView
from pai.tasks.user_errors import error_fields


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def _as_task(task: Any, d: Dict[str, Any]) -> Optional[Task]:
    """The Task behind a payload (or None if it cannot be read as one)."""
    if isinstance(task, Task):
        return task
    if d.get("id") and d.get("status"):
        try:
            return Task(**upgrade_legacy_payload(d))
        except Exception:
            return None
    return None


def _control_fields(task: Any, d: Dict[str, Any]) -> Dict[str, Any]:
    """controls / can_pause / can_resume / can_cancel / cancel_outcome / control_blocked_reason
    / retry.

    Computed by the Task model (one place), so ``retry`` can never disagree with ``status``.
    A payload that is not a task gets the honest "nothing offered" defaults instead of guessing.
    """
    t = _as_task(task, d)
    if t is None:
        out = TaskControlView(controls=TaskControls(), can_pause=False, can_resume=False,
                              can_cancel=False).model_dump(mode="json")
        out["retry"] = None
        return out
    out = t.control_view().model_dump(mode="json")
    info = t.retry_info
    out["retry"] = info.model_dump(mode="json") if info else None   # {attempt, max_attempts, next_retry_at, delay_seconds, last_error}
    return out


def serialize_task(task: Any) -> Dict[str, Any]:
    if hasattr(task, "to_dict"):
        d = task.to_dict()
    elif isinstance(task, dict):
        d = task
    else:
        d = {k: v for k, v in getattr(task, "__dict__", {}).items()
             if not k.startswith("_")}

    status = d.get("status")
    if status is not None:
        status = str(status)

    def _ts(k):
        v = d.get(k)
        return _iso(v) if v is not None else None

    def _enum(k):
        v = d.get(k)
        return str(getattr(v, "value", v)) if v is not None else None

    raw_error = d.get("error") or d.get("last_error")
    if status == "cancelled" and not raw_error:
        raw_error = "cancelled"                          # stopped on purpose: say so, not "no reason"
    # Plain-language version of the error. A FAILED task always gets one, even if the executor
    # reported nothing, so a client never has to show a bare "Failed".
    error_info = (error_fields(raw_error, d.get("capability") or d.get("task_type"))
                  if (raw_error or status == "failed") else None)

    return {
        **_control_fields(task, d),
        "id": d.get("id") or d.get("task_id") or "",
        "name": d.get("name"),
        "title": d.get("title") or d.get("name") or d.get("input") or d.get("capability") or "",
        "status": status,
        "type": _enum("type"),
        "source": _enum("source"),
        "priority": _enum("priority"),
        "user_id": d.get("user_id"),
        "session_id": d.get("session_id"),
        "capability": d.get("capability") or d.get("task_type"),
        "plugin": d.get("plugin"),
        "device_id": d.get("device_id"),
        "executor_id": d.get("executor_id"),
        "created_at": _ts("created_at"),
        "initialized_at": _ts("initialized_at"),
        "queued_at": _ts("queued_at"),
        "started_at": _ts("started_at"),
        "completed_at": _ts("completed_at"),
        "error": d.get("error") or d.get("last_error"),
        "error_info": error_info,        # {code, kind, message, hint, raw} or null
        "progress": d.get("progress"),
        "input": d.get("input"),
        "parameters": d.get("parameters"),
        "metadata": d.get("metadata"),
        "result": d.get("result"),
        "timeout": d.get("timeout"),
        "max_retries": d.get("max_retries"),
        "retry_count": d.get("retry_count"),
        "requires_confirmation": d.get("requires_confirmation"),
        "confirmation_reason": d.get("confirmation_reason"),
        # While the task waits for the user: what they approve + the scopes the server allows.
        "confirmation": d.get("confirmation"),
        "parent_task_id": d.get("parent_task_id"),
        "child_task_ids": d.get("child_task_ids"),
    }
