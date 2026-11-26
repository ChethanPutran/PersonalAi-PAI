"""Folder rules enforced on the device: symlinks, '..', prefix siblings, '~', read-only, defaults."""
import os
import stat

from pai_device.path_policy import PathPolicy, apply_roots_env, is_within
from pai_device.plugin_config_store import PluginConfigStore


def mk(tmp_path):
    allowed = tmp_path / "Android"
    allowed.mkdir()
    (allowed / "secret").mkdir()
    sibling = tmp_path / "Androidx"
    sibling.mkdir()
    return allowed, sibling


def test_inside_allowed_is_fine_and_new_files_resolve_through_the_parent(tmp_path):
    allowed, _ = mk(tmp_path)
    p = PathPolicy(allowed=[str(allowed)])
    assert p.check("files.read", {"path": str(allowed / "a.txt")}) is None
    assert p.check("files.write", {"path": str(allowed / "new" / "deep.txt")}) is None
    assert p.check("files.list", {"path": str(allowed)}) is None


def test_prefix_sibling_is_outside(tmp_path):
    allowed, sibling = mk(tmp_path)
    p = PathPolicy(allowed=[str(allowed)])
    assert "outside" in p.check("files.read", {"path": str(sibling / "x")})
    assert not is_within(str(sibling), str(allowed))


def test_dotdot_cannot_escape(tmp_path):
    allowed, sibling = mk(tmp_path)
    p = PathPolicy(allowed=[str(allowed)])
    assert p.check("files.read", {"path": f"{allowed}/../Androidx/x"}) is not None
    assert p.check("files.read", {"path": f"{allowed}/secret/../a"}) is None


def test_symlink_inside_allowed_pointing_out_is_refused(tmp_path):
    allowed, sibling = mk(tmp_path)
    (allowed / "link").symlink_to(sibling)
    p = PathPolicy(allowed=[str(allowed)])
    assert p.check("files.read", {"path": str(allowed / "link" / "f")}) is not None
    assert p.check("files.read", {"path": str(allowed / "link")}) is not None


def test_denied_wins_inside_allowed_and_through_symlinks(tmp_path):
    allowed, _ = mk(tmp_path)
    (allowed / "alias").symlink_to(allowed / "secret")
    p = PathPolicy(allowed=[str(allowed)], denied=[str(allowed / "secret")])
    assert "never" in p.check("files.read", {"path": str(allowed / "secret" / "k")})
    assert p.check("files.read", {"path": str(allowed / "alias" / "k")}) is not None
    assert p.check("files.read", {"path": str(allowed / "ok")}) is None


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Documents").mkdir()
    p = PathPolicy(allowed=["~/Documents"], denied=["~/Documents/private"])
    assert p.check("files.read", {"path": "~/Documents/a.txt"}) is None
    assert p.check("files.read", {"path": "~/Documents/private/a"}) is not None
    assert p.check("files.read", {"path": "~/Other/a"}) is not None


def test_every_path_parameter_is_checked(tmp_path):
    allowed, sibling = mk(tmp_path)
    p = PathPolicy(allowed=[str(allowed)])
    for key in ("path", "source", "destination", "outputPath"):
        assert p.check("files.copy", {key: str(sibling)}) is not None, key
    assert p.check("files.copy", {"source": str(allowed / "a"), "destination": str(sibling / "b")}) is not None
    assert p.check("files.copy", {"source": str(allowed / "a"), "destination": str(allowed / "b")}) is None
    assert p.check("files.read", {"path": 5}) is not None
    assert p.check("files.read", {"path": "a\x00b"}) is not None


def test_relative_paths_are_resolved_not_trusted(tmp_path, monkeypatch):
    allowed, _ = mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    p = PathPolicy(allowed=[str(allowed)])
    assert p.check("files.read", {"path": "Android/a"}) is None
    assert p.check("files.read", {"path": "Androidx/a"}) is not None


def test_read_only_blocks_changes_but_not_reads(tmp_path):
    allowed, _ = mk(tmp_path)
    p = PathPolicy(allowed=[str(allowed)], read_only=True)
    for cap in ("files.write", "files.delete", "files.move", "files.copy"):
        assert "read-only" in p.check(cap, {"path": str(allowed / "a")})
    assert p.check("files.read", {"path": str(allowed / "a")}) is None


def test_empty_allowed_falls_back_to_plugin_default_but_denied_and_readonly_still_apply(tmp_path):
    p = PathPolicy(allowed=[], denied=[str(tmp_path / "no")])
    assert p.roots_env() is None
    assert p.check("files.read", {"path": str(tmp_path / "anywhere")}) is None
    assert p.check("files.read", {"path": str(tmp_path / "no" / "x")}) is not None
    assert PathPolicy(allowed=[], read_only=True).check("files.write", {"path": "/x"}) is not None


def test_from_values_ignores_junk():
    p = PathPolicy.from_values({"allowedPaths": ["/a", 3, " "], "deniedPaths": "x", "readOnly": "yes"})
    assert p == PathPolicy(allowed=["/a"], denied=[], read_only=False)


def test_roots_env_is_semicolon_list_and_applied_to_environment(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.delenv("PAI_FILES_ROOTS", raising=False)
    p = PathPolicy(allowed=[str(a), str(b)])
    assert p.roots_env() == f"{os.path.realpath(a)};{os.path.realpath(b)}"
    apply_roots_env("files", p)
    assert os.environ["PAI_FILES_ROOTS"] == p.roots_env()
    apply_roots_env("files", PathPolicy(allowed=[]))             # back to the plugin default
    assert "PAI_FILES_ROOTS" not in os.environ
    apply_roots_env("camera", p)                                 # other plugins: untouched
    assert "PAI_FILES_ROOTS" not in os.environ


def test_store_is_atomic_private_and_reloads(tmp_path):
    s = PluginConfigStore(tmp_path / "plugin_config")
    s.save("files", {"allowedPaths": ["/a"], "readOnly": True})
    f = tmp_path / "plugin_config" / "files.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert not list((tmp_path / "plugin_config").glob(".tmp-*"))
    (tmp_path / "plugin_config" / "bad.json").write_text("{nope")
    assert PluginConfigStore(tmp_path / "plugin_config").load_all() == {"files": {"allowedPaths": ["/a"], "readOnly": True}}
    s.delete("files")
    assert "files" not in s.load_all()
    import pytest
    with pytest.raises(ValueError):
        s.save("../evil", {})
