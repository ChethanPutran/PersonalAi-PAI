"""pai-device plugins config: show, --set (incl. key+= / key-=), --reset, and the server's 422 messages."""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx  # noqa: E402

from pai_device import cli, cli_settings  # noqa: E402

URL = "/plugins/device/dev-1/files/config"
SCHEMA = {"type": "object", "properties": {
    "allowedPaths": {"type": "array", "title": "Folders PAI may use", "description": "Only these.",
                     "items": {"type": "string"}},
    "readOnly": {"type": "boolean", "title": "Read-only"},
    "limit": {"type": "integer"}}}
STATE = {"schema": SCHEMA, "values": {}, "defaults": {"allowedPaths": ["~/Documents"], "readOnly": False},
         "effective": {"allowedPaths": ["~/Documents", "~/Downloads"], "readOnly": False}}


def ns(**kw):
    base = dict(action="config", plugin_id="files", json=False, device=None, set=None, reset=None, no_device=True)
    base.update(kw)
    return argparse.Namespace(**base)


def ctx_for(routes, tmp_path):
    return make_ctx(make_client(routes), tmp_path)


@pytest.mark.asyncio
async def test_show(tmp_path, capsys):
    r = Routes()
    r.add("GET", URL, json_body=STATE)
    assert await cli_settings.cmd_plugins(ns(), ctx_for(r, tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Folders PAI may use" in out and "Only these." in out
    assert "~/Documents, ~/Downloads" in out and "default : ~/Documents" in out and "(default)" in out


@pytest.mark.asyncio
async def test_set_sends_only_changed_keys_with_typed_values(tmp_path, capsys):
    r = Routes()
    r.add("GET", URL, json_body=STATE)
    r.add("PUT", URL, json_body={**STATE, "delivered": True})
    code = await cli_settings.cmd_plugins(
        ns(set=["readOnly=on", "limit=5", "allowedPaths+=/data/x", "allowedPaths+=~/Pictures"]), ctx_for(r, tmp_path))
    assert code == 0
    assert r.bodies("PUT", URL) == [{"values": {
        "readOnly": True, "limit": 5, "allowedPaths": ["~/Documents", "~/Downloads", "/data/x", "~/Pictures"]}}]


@pytest.mark.asyncio
async def test_remove_replace_json_and_reset(tmp_path):
    r = Routes()
    r.add("GET", URL, json_body=STATE)
    r.add("PUT", URL, json_body={**STATE, "delivered": True})
    await cli_settings.cmd_plugins(ns(set=["allowedPaths-=~/Downloads/"]), ctx_for(r, tmp_path))
    await cli_settings.cmd_plugins(ns(set=['allowedPaths=["/a","/b"]']), ctx_for(r, tmp_path))
    await cli_settings.cmd_plugins(ns(set=["allowedPaths=/c, /d"], reset=["readOnly"]), ctx_for(r, tmp_path))
    assert r.bodies("PUT", URL) == [
        {"values": {"allowedPaths": ["~/Documents"]}},
        {"values": {"allowedPaths": ["/a", "/b"]}},
        {"values": {"allowedPaths": ["/c", "/d"], "readOnly": None}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["nokey", "unknown=1", "readOnly=maybe", "limit=x", "readOnly+=a", "allowedPaths=[1"])
async def test_bad_edits_are_explained_and_nothing_is_sent(tmp_path, capsys, bad):
    r = Routes()
    r.add("GET", URL, json_body=STATE)
    assert await cli_settings.cmd_plugins(ns(set=[bad]), ctx_for(r, tmp_path)) == 2
    assert "✗" in capsys.readouterr().err and r.count("PUT", URL) == 0


@pytest.mark.asyncio
async def test_server_422_messages_are_shown_per_field(tmp_path, capsys):
    r = Routes()
    r.add("GET", URL, json_body=STATE)
    r.add("PUT", URL, status=422, json_body={"detail": {"code": "invalid_plugin_config",
                                                         "errors": {"allowedPaths": "must not contain '..'"}}})
    assert await cli_settings.cmd_plugins(ns(set=["allowedPaths+=/a/../b"]), ctx_for(r, tmp_path)) == 1
    err = capsys.readouterr().err
    assert "allowedPaths: must not contain '..'" in err


@pytest.mark.asyncio
async def test_device_option_and_no_settings(tmp_path, capsys):
    r = Routes()
    r.add("GET", "/plugins/device/other/files/config", json_body={**STATE, "schema": {}})
    assert await cli_settings.cmd_plugins(ns(device="other"), ctx_for(r, tmp_path)) == 0
    assert "no settings" in capsys.readouterr().out


def test_parser_accepts_config_flags():
    a = cli._build_parser().parse_args(
        ["plugins", "config", "files", "--device", "d", "--set", "a=1", "--set", "b+=/x", "--reset", "c"])
    assert (a.action, a.plugin_id, a.device, a.set, a.reset) == ("config", "files", "d", ["a=1", "b+=/x"], ["c"])
