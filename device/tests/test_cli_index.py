"""pai-device index status / now / clear."""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from cli_kit import Routes, make_client, make_ctx  # noqa: E402

from pai_device import cli_index  # noqa: E402

CFG = "/plugins/device/dev-1/files/config"


def ns(action, **kw):
    base = dict(command="index", action=action, json=False, device=None)
    base.update(kw)
    return argparse.Namespace(**base)


def routes(tmp_path, on=True):
    r = Routes()
    r.add("GET", CFG, json_body={"effective": {"allowedPaths": [str(tmp_path / "docs")], "indexDocuments": on,
                                               "deniedPaths": [], "indexMaxFileMb": 20}})
    r.add("GET", "/documents/files", json_body={"files": {}})
    r.add("GET", "/documents/status", json_body={"files": 3, "chunks": 40})
    r.add("POST", "/documents/index", json_body={"chunks_stored": 1})
    r.add("POST", "/documents/remove", json_body={"removed": 3})
    return r


async def test_status(tmp_path, capsys):
    r = routes(tmp_path)
    assert await cli_index.cmd_index(ns("status"), make_ctx(make_client(r), tmp_path)) == 0
    out = capsys.readouterr().out
    assert "3 files, 40 chunks" in out and "PDF support" in out


async def test_now_reports_progress_and_skips(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("a note with enough words in it to count as text")
    r = routes(tmp_path)
    assert await cli_index.cmd_index(ns("now"), make_ctx(make_client(r), tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Sent            : 1 files, 1 chunks" in out and "Skipped" in out and r.count("POST", "/documents/index") == 1


async def test_now_when_off_sends_nothing(tmp_path, capsys):
    r = routes(tmp_path, on=False)
    assert await cli_index.cmd_index(ns("now"), make_ctx(make_client(r), tmp_path)) == 1
    assert r.count("POST", "/documents/index") == 0 and "off" in capsys.readouterr().out


async def test_clear(tmp_path, capsys):
    r = routes(tmp_path)
    ctx = make_ctx(make_client(r), tmp_path)
    assert await cli_index.cmd_index(ns("clear"), ctx) == 0
    assert r.bodies("POST", "/documents/remove") == [{"device_id": "dev-1"}]
