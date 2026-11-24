"""The Flutter Linux runner's consent -> plugin env-var bridge (PermissionManager), built standalone.

`input` and `screen` are consent-required permissions; allowing one sets the env var the plugin gates on
(PAI_INPUT_CONSENT / PAI_SCREEN_CONSENT), and a revoke clears it at once. The real C++ class is compiled
into a small test binary: no Flutter, no display, and the user's real permissions.json is never touched.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PERMS = ROOT / "frontend" / "linux" / "runner" / "agent" / "permissions"


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    if not shutil.which("g++"):
        pytest.skip("g++ not available")
    out = tmp_path_factory.mktemp("perm") / "permission_manager_test"
    build = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-I", str(PERMS), "-o", str(out),
                            str(PERMS / "permission_manager_test.cc"), str(PERMS / "permission_manager.cc")],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    return out


def run(binary, config_dir: Path):
    env = {"XDG_CONFIG_HOME": str(config_dir), "HOME": str(config_dir), "PATH": "/usr/bin:/bin"}
    return subprocess.run([str(binary)], env=env, capture_output=True, text=True)


def test_input_consent_bridge_grant_revoke_and_persistence(binary, tmp_path):
    r = run(binary, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "OK"


def test_the_users_real_permissions_file_is_never_touched(binary, tmp_path):
    real = Path.home() / ".config" / "pai" / "permissions.json"
    before = real.read_bytes() if real.exists() else None
    run(binary, tmp_path)
    assert (tmp_path / "pai" / "permissions.json").exists()          # everything went to the temp config dir
    assert (real.read_bytes() if real.exists() else None) == before  # and the real file is byte-for-byte unchanged


def test_decisions_file_records_what_the_user_chose(binary, tmp_path):
    run(binary, tmp_path)
    text = (tmp_path / "pai" / "permissions.json").read_text()
    assert '"input"' in text and '"allow"' in text and '"screen"' in text and '"deny"' in text
