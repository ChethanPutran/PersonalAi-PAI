"""invoke (incl. the approval path) and text chat (SSE parsing, streaming, confirmations, REPL)."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx, sse_text  # noqa: E402

from pai_device import chatcli, cli  # noqa: E402
from pai_device.transport import iter_sse  # noqa: E402


def inv(**kw):
    base = dict(command="invoke", capability="files.read", param=None, json_params=None, timeout=30.0, approve=False,
                scope="once", json=False, device=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _tty(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)          # invoke only reads stdin when it is piped


CONFIRM = {"capability": "files.read", "plugin_id": "files", "device_id": "dev-1", "risk": "medium",
           "reason": "Reads a file", "allowed_scopes": ["once", "session"],
           "parameters_summary": [{"name": "path", "value_preview": "/home/me/notes.txt"}]}


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_sends_typed_params_and_prints_the_plugin_payload(capsys):
    r = Routes()
    r.add("POST", "/devices/dev-1/invoke", json_body={
        "task_id": "t1", "status": "completed", "ok": True, "device_id": "dev-1", "capability": "files.read",
        "result": {"ok": True, "data": {"text": "hello"}}})
    code = await cli._cmd_invoke(inv(param=["path=/tmp/a", "limit=3"], json_params='{"mode": "r"}'),
                                 make_client(r), "dev-1")
    assert code == 0 and '"text": "hello"' in capsys.readouterr().out
    body = r.bodies("POST", "/devices/dev-1/invoke")[0]
    assert body["parameters"] == {"mode": "r", "path": "/tmp/a", "limit": 3}
    assert body["capability"] == "files.read" and body["timeout_seconds"] == 30.0 and body["wait_seconds"] == 30.0


@pytest.mark.asyncio
async def test_invoke_failure_shows_the_error_and_exits_nonzero(capsys):
    r = Routes()
    r.add("POST", "/devices/dev-1/invoke", json_body={
        "task_id": "t1", "status": "failed", "ok": False, "device_id": "dev-1", "capability": "files.read",
        "error": "File not found"})
    assert await cli._cmd_invoke(inv(), make_client(r), "dev-1") == 1
    assert "✗ File not found" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_invoke_needing_confirmation_shows_details_and_does_not_run_without_approve(capsys):
    r = Routes()
    r.add("POST", "/devices/dev-1/invoke", status=202, json_body={
        "task_id": "t9", "status": "paused", "ok": False, "device_id": "dev-1", "capability": "files.read",
        "awaiting_confirmation": True, "confirmation": CONFIRM})
    code = await cli._cmd_invoke(inv(), make_client(r), "dev-1")
    cap = capsys.readouterr()
    assert code == 3
    assert "Approval needed: files.read" in cap.out and "/home/me/notes.txt" in cap.out and "pai-device approve t9" in cap.out
    assert "nothing ran" in cap.err
    assert r.count("POST", "/tasks/t9/confirm") == 0                # never approved silently


@pytest.mark.asyncio
async def test_invoke_with_approve_confirms_then_waits_for_the_result(capsys):
    r = Routes()
    r.add("POST", "/devices/dev-1/invoke", status=202, json_body={
        "task_id": "t9", "status": "paused", "ok": False, "device_id": "dev-1", "capability": "files.read",
        "awaiting_confirmation": True, "confirmation": CONFIRM})
    r.add("POST", "/tasks/t9/confirm", json_body={"status": "confirmed"})
    r.add("GET", "/tasks/t9", json_body={"task": {"status": "completed",
                                                  "result": {"success": True, "output": {"text": "hi"}}}})
    code = await cli._cmd_invoke(inv(approve=True, scope="session"), make_client(r), "dev-1")
    assert code == 0 and '"text": "hi"' in capsys.readouterr().out
    assert r.bodies("POST", "/tasks/t9/confirm") == [{"scope": "session"}]


@pytest.mark.asyncio
async def test_invoke_bad_params_never_reach_the_server(capsys):
    r = Routes()
    assert await cli._cmd_invoke(inv(param=["oops"]), make_client(r), "dev-1") == 2
    assert await cli._cmd_invoke(inv(json_params="{bad"), make_client(r), "dev-1") == 2
    assert not r.calls
    assert await cli._cmd_invoke(inv(), make_client(r), None) == 1   # no device id


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

async def _lines(text):
    for line in text.split("\n"):
        yield line


@pytest.mark.asyncio
async def test_iter_sse_parses_events_comments_json_and_multiline_data():
    text = (": keep-alive\n\nretry: 3000\n\nevent: delta\ndata: {\"delta\": \"Hi\"}\n\n"
            "event: done\ndata: {\"status\": \"completed\"}\n\nevent: note\ndata: line1\ndata: line2\n\n"
            "event: tail\ndata: {\"x\": 1}")
    got = [item async for item in iter_sse(_lines(text))]
    assert got == [("delta", {"delta": "Hi"}), ("done", {"status": "completed"}),
                   ("note", "line1\nline2"), ("tail", {"x": 1})]


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------

def chat_stream(*events):
    return sse_text(*events)


DONE_OK = ("done", {"message": "Hello there", "status": "completed", "session_id": "s1",
                    "tasks": [{"capability": "chat.reply", "status": "completed"},
                              {"capability": "camera.capture", "status": "completed", "device_id": "devdevdev1"}]})


@pytest.mark.asyncio
async def test_chat_streams_tokens_status_on_stderr_and_reports_tools(tmp_path, capsys):
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(
        ("status", {"stage": "planning", "message": "Understanding your request..."}),
        ("delta", {"delta": "Hello "}), ("delta", {"delta": "there"}), DONE_OK))
    ctx = make_ctx(make_client(r), tmp_path)
    result = await chatcli.run_turn(ctx, "dev-1", "s1", "hi")
    chatcli._report(result)
    cap = capsys.readouterr()
    assert cap.out == "Hello there\n"                                # tokens only on stdout
    assert "Understanding your request" in cap.err and "✓ camera.capture on devdevde" in cap.err
    assert "chat.reply" not in cap.err and result.ok
    assert r.bodies("POST", "/chat/stream") == [
        {"device_id": "dev-1", "message": "hi", "mode": "text", "session_id": "s1"}]
    assert r.calls[0].headers["accept"] == "text/event-stream"


@pytest.mark.asyncio
async def test_chat_prints_the_final_message_when_nothing_streamed_and_surfaces_errors(tmp_path, capsys):
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(("done", {"message": "Done.", "status": "completed", "session_id": "s1"})))
    ctx = make_ctx(make_client(r), tmp_path)
    await chatcli.run_turn(ctx, "dev-1", "s1", "/help")
    assert capsys.readouterr().out == "Done.\n"

    r2 = Routes()
    r2.add("POST", "/chat/stream", sse=chat_stream(("error", {"message": "Orchestrator failed: boom"})))
    res = await chatcli.run_turn(make_ctx(make_client(r2), tmp_path), "dev-1", "s1", "x")
    chatcli._report(res)
    assert not res.ok and "✗ Orchestrator failed: boom" in capsys.readouterr().err

    r3 = Routes()
    r3.add("POST", "/chat/stream", status=403, json_body={"detail": "Session not owned by user"})
    res = await chatcli.run_turn(make_ctx(make_client(r3), tmp_path), "dev-1", "s1", "x")
    assert res.error == "Session not owned by user"


@pytest.mark.asyncio
async def test_chat_confirmation_without_a_terminal_says_how_to_approve_and_never_approves(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    r = Routes()
    ctx = make_ctx(make_client(r), tmp_path)

    async def fake_sse(method, path, **kw):
        if path.endswith("/events"):                      # the approvals stream: one request, then quiet
            yield "task.started", {"id": "t0"}
            yield "task.awaiting_confirmation", {"id": "t7", "capability": "files.read", "confirmation": CONFIRM}
            await asyncio.sleep(10)
        else:                                             # the reply is still streaming when it arrives
            yield "delta", {"delta": "ok"}
            await asyncio.sleep(0.05)
            yield DONE_OK
    ctx.client.sse = fake_sse

    async def on_confirm(task):
        await chatcli.ask_in_terminal(ctx, task)
    await chatcli.run_turn(ctx, "dev-1", "s1", "read my notes", on_confirm=on_confirm)
    err = capsys.readouterr().err
    assert "Approval needed: files.read" in err and "pai-device approve t7" in err
    assert r.count("POST", "/tasks/t7/confirm") == 0 and r.count("POST", "/tasks/t7/deny") == 0


@pytest.mark.asyncio
async def test_a_dead_approvals_stream_does_not_break_the_reply(tmp_path, capsys):
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(("delta", {"delta": "fine"}), DONE_OK))
    r.add("GET", "/tasks/s1/events", status=503, json_body={"detail": "Status stream unavailable"})
    ctx = make_ctx(make_client(r), tmp_path)

    async def on_confirm(task):
        raise AssertionError("no approval was requested")
    res = await chatcli.run_turn(ctx, "dev-1", "s1", "hi", on_confirm=on_confirm)
    assert res.ok and capsys.readouterr().out == "fine\n"


@pytest.mark.asyncio
async def test_chat_confirmation_in_a_terminal_asks_and_honours_the_answer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    r = Routes()
    r.add("POST", "/tasks/t7/confirm", json_body={"status": "confirmed"})
    r.add("POST", "/tasks/t8/deny", json_body={"status": "denied"})
    ctx = make_ctx(make_client(r), tmp_path)
    answers = iter(["s", "no"])

    async def fake_read(prompt=""):
        return next(answers)
    monkeypatch.setattr(chatcli, "read_line", fake_read)
    await chatcli.ask_in_terminal(ctx, {"id": "t7", "confirmation": CONFIRM})
    await chatcli.ask_in_terminal(ctx, {"id": "t8", "confirmation": CONFIRM})
    assert r.bodies("POST", "/tasks/t7/confirm") == [{"scope": "session"}]     # 's' is offered: the server allows it
    assert r.count("POST", "/tasks/t8/deny") == 1


def chat_args(**kw):
    base = dict(command="chat", message=[], session=None, new=False, quiet=False, json=False, device=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.asyncio
async def test_one_shot_chat_remembers_the_session_for_the_next_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(("delta", {"delta": "A"}), DONE_OK))
    r.add("GET", "/tasks/s1/events", sse="")
    ctx = make_ctx(make_client(r), tmp_path)
    assert await chatcli.cmd_chat(chat_args(message=["hello", "world"]), ctx) == 0
    assert (tmp_path / "state" / "chat_session").read_text() == "s1"
    first = r.bodies("POST", "/chat/stream")[0]
    assert first["message"] == "hello world" and first["session_id"] != "s1"      # a fresh id the client chose
    await chatcli.cmd_chat(chat_args(message=["again"]), ctx)
    assert r.bodies("POST", "/chat/stream")[1]["session_id"] == "s1"               # continued
    await chatcli.cmd_chat(chat_args(message=["fresh"], new=True), ctx)
    assert r.bodies("POST", "/chat/stream")[2]["session_id"] not in ("s1",)


@pytest.mark.asyncio
async def test_chat_without_a_device_id_explains_itself(tmp_path, capsys):
    ctx = make_ctx(make_client(Routes(), None), tmp_path, device_id=None)
    assert await chatcli.cmd_chat(chat_args(message=["hi"]), ctx) == 1
    assert "pai-device run" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_repl_commands_and_turns(tmp_path, monkeypatch, capsys):
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(("delta", {"delta": "pong"}), DONE_OK))
    r.add("GET", "/tasks/s1/events", sse="")
    ctx = make_ctx(make_client(r), tmp_path)
    lines = iter(["", "/help", "/status", "/bogus", "ping", "/new", "/quit"])

    async def fake_read(prompt=""):
        return next(lines)
    monkeypatch.setattr(chatcli, "read_line", fake_read)
    code = await chatcli.repl(chat_args(), ctx, "dev-1", "sess", None)
    cap = capsys.readouterr()
    assert code == 0 and "pong" in cap.out
    assert "session sess" in cap.err and "unknown command '/bogus'" in cap.err
    assert len(r.bodies("POST", "/chat/stream")) == 1                 # only "ping" was sent as a message


@pytest.mark.asyncio
async def test_repl_ends_on_end_of_input(tmp_path, monkeypatch):
    ctx = make_ctx(make_client(Routes()), tmp_path)

    async def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr(chatcli, "read_line", eof)
    assert await chatcli.repl(chat_args(), ctx, "dev-1", "s", None) == 0


@pytest.mark.asyncio
async def test_a_progress_line_never_cuts_a_sentence_in_half(tmp_path, capsys):
    r = Routes()
    r.add("POST", "/chat/stream", sse=chat_stream(
        ("delta", {"delta": "Hello"}), ("status", {"stage": "done", "message": "Done"}),
        ("delta", {"delta": " again"}), DONE_OK))
    await chatcli.run_turn(make_ctx(make_client(r), tmp_path), "dev-1", "s1", "hi")
    out = capsys.readouterr().out
    assert out == "Hello\n again\n"                      # the status forced a line break, no double newline at the end
