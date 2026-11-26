"""Every failure reaches the user in plain words (never a bare "Failed", never raw developer text only)."""
import pytest

from pai.orchestration.orchestrator import TaskOrchestrator
from pai.tasks.serialization import serialize_task
from pai.tasks.user_errors import explain_error


@pytest.mark.parametrize("raw,code,kind", [
    ("Authorization denied: Path is outside allowed roots", "path_outside_roots", "denied"),
    ("Authorization denied: No filesystem roots configured", "no_roots", "denied"),
    ("Authorization denied: Filesystem permission requires a path", "path_missing", "denied"),
    ("Authorization denied: Plugin is not enabled for this user.", "plugin_disabled", "denied"),
    ("Authorization denied: Device is not allowlisted", "device_not_allowed", "denied"),
    ("Authorization denied: High-risk action requires approval", "needs_approval", "declined"),
    ("Authorization not granted: Consent is missing or inactive", "needs_approval", "declined"),
    ("User denied confirmation", "user_declined", "declined"),
    ("Invalid authorization token: expired", "token_invalid", "denied"),
    ("Device d1 did not respond in 60.0s", "timeout", "timeout"),
    ("Task timed out after 30 seconds", "timeout", "timeout"),
    ("Executor is not running", "device_unavailable", "unavailable"),
    ("Task has no device_id", "device_unavailable", "unavailable"),
    ("Task cancelled", "cancelled", "cancelled"),
    ("open(/x): Permission denied", "os_permission", "denied"),
    ("No such file or directory", "not_found", "failed"),
])
def test_known_errors_get_a_plain_explanation(raw, code, kind):
    e = explain_error(raw)
    assert (e.code, e.kind) == (code, kind) and e.raw == raw
    assert e.message and "Authorization" not in e.message and "Traceback" not in e.message


def test_an_unknown_error_is_shown_not_hidden_and_loses_its_technical_prefix():
    e = explain_error("Authorization denied: the moon is in the wrong phase", "camera.capture")
    assert e.code == "unknown" and "moon is in the wrong phase" in e.message and "camera.capture" in e.message
    assert "Authorization denied" not in e.message


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_empty_error_is_explained_not_blank(empty):
    e = explain_error(empty)
    assert e.code == "no_reason" and e.message and e.hint


def test_denials_that_can_be_fixed_say_what_to_do():
    for raw in ("Authorization denied: Path is outside allowed roots", "Device d1 did not respond in 5s",
                "Authorization denied: Plugin is not enabled for this user."):
        assert explain_error(raw).hint


def test_serialized_failed_task_carries_error_info_even_with_no_error():
    base = {"id": "t1", "status": "failed", "capability": "files.read"}
    assert serialize_task({**base, "error": "Authorization denied: Path is outside allowed roots"}
                          )["error_info"]["code"] == "path_outside_roots"
    assert serialize_task(base)["error_info"]["code"] == "no_reason"          # a bare "Failed" never reaches the UI


def test_serialized_cancelled_and_healthy_tasks():
    assert serialize_task({"id": "t", "status": "cancelled"})["error_info"]["kind"] == "cancelled"
    assert serialize_task({"id": "t", "status": "completed"})["error_info"] is None


def test_plan_results_get_user_messages_so_the_chat_reply_can_report_them():
    results = [
        {"task_id": "a", "success": True, "output": "ok"},
        {"task_id": "b", "success": False, "error": "Authorization denied: Path is outside allowed roots"},
        {"task_id": "c", "status": "skipped", "error": "dependency b failed"},
        {"task_id": "d", "status": "failed", "capability": "camera.capture", "error": ""},
    ]
    TaskOrchestrator._explain_failures(results)
    a, b, c, d = results
    assert "user_message" not in a
    assert b["error_kind"] == "denied" and "allowed to use" in b["user_message"] and b["hint"]
    assert "earlier step" in c["user_message"]
    assert "nothing reported why" in d["user_message"]
    TaskOrchestrator._explain_failures(results)           # idempotent
    assert b["user_message"].count("allowed to use") == 1


async def test_task_events_carry_error_info_too():
    """`task.failed` over the live stream must explain itself exactly like the REST view."""
    from pai.tasks.manager import TaskManager
    seen = []

    async def fake_super_publish(self, event_type, data=None, **kw):
        seen.append((event_type, data))

    from pai.core.lifecycle import Lifecycle
    orig = Lifecycle._publish
    Lifecycle._publish = fake_super_publish
    try:
        tm = TaskManager.__new__(TaskManager)
        await tm._publish("task.failed", {"id": "t1", "status": "failed", "capability": "files.read",
                                          "error": "Authorization denied: Path is outside allowed roots"})
        await tm._publish("task.failed", {"id": "t2", "status": "failed"})
        await tm._publish("task.started", {"id": "t3", "status": "running"})
        await tm._publish("dag.finished", {"id": "x", "status": "failed"})
    finally:
        Lifecycle._publish = orig
    by_id = {d["id"]: d for _, d in seen}
    assert by_id["t1"]["error_info"]["code"] == "path_outside_roots"
    assert by_id["t2"]["error_info"]["code"] == "no_reason"
    assert "error_info" not in by_id["t3"] and "error_info" not in by_id["x"]


def test_a_hard_policy_block_is_explained_not_called_a_restriction():
    raw = "Authorization denied: Operation is blocked by system restrictions; Critical-risk operation blocked"
    e = explain_error(raw, "system.execute")
    assert e.code == "blocked_by_policy" and "safety rule" in e.message and e.raw == raw
    assert explain_error(raw, "files.read").code == "blocked_by_policy"


def test_a_failed_plan_shows_the_reply_or_the_reason_never_a_bare_task_id():
    from pai.api.routes.chat import _format_chat_message as fmt
    reply = {"capability": "chat.reply", "status": "completed", "reply": "I can't run shell commands."}
    boom = {"capability": "shell.exec", "status": "failed", "user_message": "Blocked.", "hint": "Try X."}
    assert fmt("g", "failed", [boom, reply], ["task_1"]) == "I can't run shell commands."
    assert fmt("g", "failed", [boom], ["task_1"]) == "Blocked. Try X."
    assert "task_1" in fmt("g", "failed", [{"status": "failed"}], ["task_1"])      # last resort only
