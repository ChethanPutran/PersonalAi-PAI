"""The Settings page's form model, run (with node) against the REAL Config schema."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pai.config import settings_admin as S
from pai.config.app import Config

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_settings_form_model_against_the_real_schema(tmp_path):
    schema = Config.model_json_schema()
    schema = {**schema, "properties": {k: v for k, v in schema["properties"].items() if k != "secrets"}}
    f = tmp_path / "schema.json"
    f.write_text(json.dumps({"schema": schema, "secret_paths": S.secret_paths(schema), "known_paths": sorted(S.known_paths(schema))}))
    import os
    env = {**os.environ, "PAI_SCHEMA_JSON": str(f)}
    # the test reads {schema, secret_paths, known_paths}
    r = subprocess.run(["node", "--test", str(REPO / "tests/admin/js/settings_schema.test.mjs")], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout[-3500:] + r.stderr[-1500:]
    assert "# skipped 0" in r.stdout or "skipped 0" in r.stdout
