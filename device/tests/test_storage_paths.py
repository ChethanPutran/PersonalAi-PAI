"""Where the agent stores things, and what it says when it cannot write there."""
import os
import stat

import pytest

from pai_device.config import DeviceConfig, StorageNotWritable, ensure_writable_dir, pai_home


def test_pai_home_env_redirects_state_and_plugins(tmp_path, monkeypatch):
    monkeypatch.setenv("PAI_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("PAI_STATE_DIR", raising=False)
    monkeypatch.delenv("PAI_PLUGIN_DIR", raising=False)
    cfg = DeviceConfig(backend_url="http://x", email="e", password="p", device_name="d")
    assert cfg.state_dir == tmp_path / "data" / "state" and cfg.plugin_dir == tmp_path / "data" / "plugins"
    monkeypatch.setenv("PAI_STATE_DIR", str(tmp_path / "s"))
    assert DeviceConfig(backend_url="x", email="e", password="p", device_name="d").state_dir == tmp_path / "s"


def test_a_writable_dir_is_created_and_left_clean(tmp_path):
    d = ensure_writable_dir(tmp_path / "a" / "b", "state")
    assert d.is_dir() and list(d.iterdir()) == []                 # the write probe is removed


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_dir_explains_the_fix_instead_of_a_traceback(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)                     # like a root-owned Docker volume
    try:
        with pytest.raises(StorageNotWritable) as err:
            ensure_writable_dir(locked / "state", "state")
        msg = str(err.value)
        assert "not writable" in msg and "docker volume rm pai-device" in msg and "PAI_HOME" in msg
    finally:
        locked.chmod(stat.S_IRWXU)


def test_no_home_at_all_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("PAI_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no home"))))
    with pytest.raises(StorageNotWritable, match="PAI_HOME"):
        pai_home()
