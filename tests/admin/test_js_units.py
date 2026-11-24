"""Run the admin UI's pure-JS unit tests (layout, validation, SSE parser, reducers) with node."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).parent / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_admin_ui_javascript_units():
    files = sorted(str(p) for p in JS_DIR.glob("*.test.mjs"))
    assert files, "no JS test files found"
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
