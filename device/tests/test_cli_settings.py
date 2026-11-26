"""settings / remote-access / profile / password / devices / plugins / operations / approve / config."""
import argparse
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx  # noqa: E402

from pai_device import cli, cli_settings  # noqa: E402
from pai_device.cli_support import error_text, parse_on_off, parse_params  # noqa: E402


def ns(**kw):
    base = dict(json=False, device=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def routes():
    return Routes()


def ctx_for(routes, tmp_path, device_id="dev-1"):
    return make_ctx(make_client(routes, device_id), tmp_path, device_id)


@pytest.mark.asyncio
async def test_remote_access_show_then_set_sends_only_what_changed(routes, tmp_path, capsys):
    state = {"device_id": "dev-1", "name": "Laptop", "allow_remote_install": False, "allow_remote_invoke": False}
    routes.add("GET", "/devices/dev-1/remote-access", json_body=state)
    routes.add("PUT", "/devices/dev-1/remote-access", json_body={**state, "allow_remote_install": True})
    ctx = ctx_for(routes, tmp_path)

    assert await cli_settings.cmd_remote_access(ns(action="show"), ctx) == 0
    out = capsys.readouterr()
    assert "allow remote plugin install : off" in out.out and "OFF by default" in out.err

    assert await cli_settings.cmd_remote_access(ns(action="set", install="on", invoke=None), ctx) == 0
    assert routes.bodies("PUT", "/devices/dev-1/remote-access") == [{"allow_remote_install": True}]
    assert "install : on" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_remote_access_set_needs_a_switch_and_a_valid_value(routes, tmp_path, capsys):
    ctx = ctx_for(routes, tmp_path)
    assert await cli_settings.cmd_remote_access(ns(action="set", install=None, invoke=None), ctx) == 2
    with pytest.raises(ValueError):
        await cli_settings.cmd_remote_access(ns(action="set", install="maybe", invoke=None), ctx)
    assert routes.count("PUT", "/devices/dev-1/remote-access") == 0


@pytest.mark.asyncio
async def test_remote_access_without_a_registered_device_explains_what_to_do(routes, tmp_path, capsys):
    ctx = ctx_for(routes, tmp_path, device_id=None)
    assert await cli_settings.cmd_remote_access(ns(action="show"), ctx) == 1
    assert "pai-device run" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_profile_show_and_set(routes, tmp_path, capsys):
    prof = {"email": "me@example.com", "display_name": None, "timezone": None, "locale": None}
    routes.add("GET", "/account/profile", json_body=prof)
    routes.add("PATCH", "/account/profile", json_body={**prof, "display_name": "Chethan", "timezone": "Asia/Kolkata"})
    ctx = ctx_for(routes, tmp_path)
    assert await cli_settings.cmd_profile(ns(action="show"), ctx) == 0
    assert "(not set)" in capsys.readouterr().out
    code = await cli_settings.cmd_profile(
        ns(action="set", name="Chethan", timezone="Asia/Kolkata", language=None), ctx)
    assert code == 0
    assert routes.bodies("PATCH", "/account/profile") == [{"display_name": "Chethan", "timezone": "Asia/Kolkata"}]
    assert await cli_settings.cmd_profile(ns(action="set", name=None, timezone=None, language=None), ctx) == 2


@pytest.mark.asyncio
async def test_password_prompts_never_takes_an_argument_and_checks_the_repeat(routes, tmp_path, monkeypatch, capsys):
    ctx = ctx_for(routes, tmp_path)
    routes.add("POST", "/account/password", json_body={"ok": True})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["old-secret-1", "new-secret-22", "new-secret-22"])
    monkeypatch.setattr(cli_settings.getpass, "getpass", lambda prompt="": next(answers))
    assert await cli_settings.cmd_password(ns(), ctx) == 0
    assert routes.bodies("POST", "/account/password") == [
        {"current_password": "old-secret-1", "new_password": "new-secret-22"}]

    answers = iter(["old-secret-1", "new-secret-22", "different-xx"])
    assert await cli_settings.cmd_password(ns(), ctx) == 1
    assert routes.count("POST", "/account/password") == 1           # a mismatch never reaches the server

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert await cli_settings.cmd_password(ns(), ctx) == 2           # no terminal: refuses


@pytest.mark.asyncio
async def test_devices_marks_this_device_and_shows_the_switches(routes, tmp_path, capsys):
    routes.add("GET", "/account/devices", json_body={"devices": [
        {"id": "dev-1", "name": "Laptop", "platform": "linux", "connected": True, "last_seen": None,
         "allow_remote_install": True, "allow_remote_invoke": False},
        {"id": "dev-2", "name": "Phone", "platform": "android", "connected": False, "last_seen": "2026-01-01T00:00:00",
         "allow_remote_install": False, "allow_remote_invoke": False}]})
    assert await cli_settings.cmd_devices(ns(), ctx_for(routes, tmp_path)) == 0
    out = capsys.readouterr().out
    assert "* Laptop" in out and "  Phone" in out and "online" in out and "offline" in out


@pytest.mark.asyncio
async def test_plugins_list_merges_this_devices_install_state_and_enable_disable_hit_the_account(routes, tmp_path, capsys):
    routes.add("GET", "/account/plugins", json_body={"plugins": [
        {"id": "camera_linux", "name": "Camera", "version": "1.0.0", "platforms": ["linux"], "enabled": True},
        {"id": "files", "name": "Files", "version": "1.0.0", "platforms": ["linux"], "enabled": False}]})
    routes.add("GET", "/plugins/device/dev-1/installed", json_body={"plugins": [
        {"plugin_id": "camera_linux", "is_installed": True, "enabled_on_device": True}]})
    routes.add("POST", "/plugins/files/enable", json_body={"status": "enabled"})
    ctx = ctx_for(routes, tmp_path)
    assert await cli_settings.cmd_plugins(ns(action="list", no_device=False), ctx) == 0
    out = capsys.readouterr().out
    assert "INSTALLED" in out and "camera_linux" in out
    assert await cli_settings.cmd_plugins(ns(action="enable", plugin_id="files", no_device=False), ctx) == 0
    assert routes.bodies("POST", "/plugins/files/enable") == [{}]     # account level: no device id
    assert await cli_settings.cmd_plugins(ns(action="disable", plugin_id=None, no_device=False), ctx) == 2


@pytest.mark.asyncio
async def test_operations_list_filters_are_sent(routes, tmp_path, capsys):
    routes.add("GET", "/plugins/operations", json_body={"operations": [
        {"id": "abcdef123456", "plugin_id": "camera", "version": "1.0.0", "kind": "install", "device_id": "dev-2xxxx",
         "status": "failed", "updated_at": "2026-01-01T10:00:00+00:00",
         "error": {"message": "The device went offline."}}]})
    code = await cli_settings.cmd_operations(ns(plugin="camera", active=True, limit=5, device="dev-2xxxx"),
                                             ctx_for(routes, tmp_path))
    assert code == 0 and "The device went offline." in capsys.readouterr().out
    q = routes.calls[-1].url.params
    assert q["device_id"] == "dev-2xxxx" and q["active"] == "true" and q["plugin_id"] == "camera" and q["limit"] == "5"


@pytest.mark.asyncio
async def test_config_show_masks_the_password(routes, tmp_path, capsys):
    ctx = ctx_for(routes, tmp_path)
    assert await cli_settings.cmd_config(ns(action="show"), ctx) == 0
    out = capsys.readouterr().out
    assert "secret-pw" not in out and "********" in out and "backend_url" in out
    assert await cli_settings.cmd_config(ns(action="show", json=True), ctx) == 0
    assert "secret-pw" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_settings_set_updates_profile_and_switches_then_shows_everything(routes, tmp_path, capsys):
    prof = {"email": "me@example.com", "display_name": "Zed", "timezone": None, "locale": "en"}
    routes.add("PATCH", "/account/profile", json_body=prof)
    routes.add("GET", "/account/profile", json_body=prof)
    routes.add("PUT", "/devices/dev-1/remote-access", json_body={})
    routes.add("GET", "/devices/dev-1/remote-access", json_body={
        "device_id": "dev-1", "name": "Laptop", "allow_remote_install": False, "allow_remote_invoke": True})
    code = await cli_settings.cmd_settings(
        ns(action="set", name="Zed", timezone=None, language="en", remote_install="off", remote_invoke="on"),
        ctx_for(routes, tmp_path))
    assert code == 0
    assert routes.bodies("PATCH", "/account/profile") == [{"display_name": "Zed", "locale": "en"}]
    assert routes.bodies("PUT", "/devices/dev-1/remote-access") == [
        {"allow_remote_install": False, "allow_remote_invoke": True}]
    assert "remote plugin use" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_approve_then_waits_for_the_task_result_and_deny_cancels(routes, tmp_path, capsys):
    routes.add("POST", "/tasks/t1/confirm", json_body={"status": "confirmed", "grant": None})
    routes.add("GET", "/tasks/t1", replies=[dict(json={"task": {"status": "running"}}),
                                            dict(json={"task": {"status": "completed",
                                                                "result": {"success": True, "output": {"ip": "1.2.3.4"}}}})])
    routes.add("POST", "/tasks/t2/deny", json_body={"status": "denied"})
    ctx = ctx_for(routes, tmp_path)
    code = await cli_settings.cmd_approve(ns(task_id="t1", scope="session", no_wait=False, timeout=30.0), ctx)
    assert code == 0 and '"ip": "1.2.3.4"' in capsys.readouterr().out
    assert routes.bodies("POST", "/tasks/t1/confirm") == [{"scope": "session"}]
    assert await cli_settings.cmd_deny(ns(task_id="t2"), ctx) == 0
    assert "will not run" in capsys.readouterr().out


def test_helpers_parse_values_and_server_messages():
    assert parse_on_off("ON") is True and parse_on_off("no") is False and parse_on_off(None) is None
    assert parse_params(["n=3", "ok=true", "name=bob", "xs=[1,2]"], '{"a": 1}') == {
        "a": 1, "n": 3, "ok": True, "name": "bob", "xs": [1, 2]}
    with pytest.raises(ValueError):
        parse_params(["novalue"], None)
    with pytest.raises(ValueError):
        parse_params([], "[1]")
    req = httpx.Request("POST", "http://t/x")
    for body, msg, code in [
        ({"detail": {"code": "remote_install_disabled", "message": "Turned off on 'PC'."}}, "Turned off on 'PC'.", "remote_install_disabled"),
        ({"detail": "Device not found"}, "Device not found", None),
        ({"detail": [{"msg": "Value error, Unknown time zone"}]}, "Unknown time zone", None),
    ]:
        exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(403, json=body, request=req))
        assert error_text(exc) == (msg, code)


def test_http_errors_are_printed_in_the_servers_words(capsys):
    req = httpx.Request("POST", "http://t/api/v1/plugins/camera/install")
    resp = httpx.Response(403, json={"detail": {"code": "remote_install_disabled",
                                                "message": "Remote plugin installation is turned off on 'PC'."}}, request=req)
    cli._print_http_error(httpx.HTTPStatusError("x", request=req, response=resp))
    err = capsys.readouterr().err
    assert "✗ Remote plugin installation is turned off on 'PC'." in err and "remote_install_disabled" in err
