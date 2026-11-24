"""The packager ships ONLY <version>/<platform>/artifact/, with files landing at
<platform>/<file> in the archive (so manifest `artifact` paths and released checksums hold)."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from pai.common.enums.device import DevicePlatform
from pai.plugins.manifest import PluginManifest
from pai.plugins.packager import PluginPackager

LINUX = DevicePlatform.LINUX


def _manifest(artifact: str = "linux/libx.so") -> PluginManifest:
    return PluginManifest.model_validate({
        "schemaVersion": 1, "id": "demo", "name": "Demo", "type": "native", "version": "1.0.0",
        "author": "PAI", "license": "MIT",
        "runtime": {"kind": "device", "nativeModule": "pai.demo"},
        "platforms": {"linux": {"artifact": artifact, "entrypoint": "create"}},
        "architectures": ["x86_64"], "deviceTypes": ["desktop"],
        "capabilities": [{"id": "demo.run", "description": "x"}],
    })


@pytest.fixture
def vdir(tmp_path) -> Path:
    d = tmp_path / "demo" / "1.0.0"
    (d / "linux" / "artifact").mkdir(parents=True)
    return d


def _put(vdir: Path, rel: str, data: bytes = b"bin") -> Path:
    p = vdir / "linux" / "artifact" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _names(pkg) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(__import__("gzip").decompress(pkg.packagePath.read_bytes()))) as t:
        return sorted(m.name for m in t.getmembers())


def _pack(tmp_path, vdir, manifest=None):
    return PluginPackager(tmp_path / "out").package(manifest or _manifest(), vdir, LINUX)


def test_ships_every_file_in_artifact_folder(tmp_path, vdir):
    _put(vdir, "libx.so")
    _put(vdir, "libhelper.so")
    _put(vdir, "assets/model.bin")
    assert _names(_pack(tmp_path, vdir)) == [
        "linux/assets/model.bin", "linux/libhelper.so", "linux/libx.so"]


def test_nothing_outside_artifact_folder_is_packaged(tmp_path, vdir):
    _put(vdir, "libx.so")
    (vdir / "linux" / "libstray.so").write_bytes(b"old build left next to the manifest")
    (vdir / "linux" / "src").mkdir()
    (vdir / "linux" / "src" / "plugin.cpp").write_text("// source")
    (vdir / "linux" / "manifest.json").write_text("{}")
    (vdir / "linux" / "tests").mkdir()
    (vdir / "linux" / "tests" / "t.py").write_text("x")
    assert _names(_pack(tmp_path, vdir)) == ["linux/libx.so"]


def test_dotfiles_are_skipped(tmp_path, vdir):
    _put(vdir, "libx.so")
    _put(vdir, ".gitkeep", b"")
    _put(vdir, ".cache/x", b"1")
    assert _names(_pack(tmp_path, vdir)) == ["linux/libx.so"]


def test_checksum_is_deterministic_and_tracks_every_file(tmp_path, vdir):
    _put(vdir, "libx.so")
    extra = _put(vdir, "extra.bin", b"v1")
    first = _pack(tmp_path, vdir)
    assert PluginPackager(tmp_path / "other").package(_manifest(), vdir, LINUX).checksum == first.checksum
    extra.write_bytes(b"v2")                                   # change a NON-main file
    assert _pack(tmp_path, vdir).checksum != first.checksum    # ...the package must change


def test_missing_artifact_folder_is_an_error_with_guidance(tmp_path):
    d = tmp_path / "demo" / "1.0.0"
    (d / "linux").mkdir(parents=True)
    (d / "linux" / "libx.so").write_bytes(b"x")                # old layout: NOT packaged
    with pytest.raises(FileNotFoundError, match="Artifact folder not found"):
        _pack(tmp_path, d)


def test_declared_artifact_must_be_in_the_folder(tmp_path, vdir):
    _put(vdir, "libother.so")
    with pytest.raises(FileNotFoundError, match="linux/artifact/libx.so"):
        _pack(tmp_path, vdir)


@pytest.mark.parametrize("bad", ["libx.so", "android/libx.so", "../libx.so", "/etc/passwd", "linux"])
def test_declared_artifact_path_must_be_under_platform(tmp_path, vdir, bad):
    _put(vdir, "libx.so")
    with pytest.raises((ValueError, FileNotFoundError)):
        _pack(tmp_path, vdir, _manifest(bad))


def test_symlinks_are_rejected(tmp_path, vdir):
    _put(vdir, "libx.so")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (vdir / "linux" / "artifact" / "innocent.so").symlink_to(secret)
    with pytest.raises(ValueError, match="Symlink"):
        _pack(tmp_path, vdir)


def test_symlinked_artifact_folder_is_rejected(tmp_path):
    d = tmp_path / "demo" / "1.0.0"
    (d / "linux").mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "libx.so").write_bytes(b"x")
    (d / "linux" / "artifact").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinked artifact folder"):
        _pack(tmp_path, d)


def test_registry_skips_version_without_artifact_folder_but_keeps_others(tmp_path):
    import json
    from pai.plugins.registry import PluginRegistry
    from pai.plugins.store import LocalArtifactStore
    import asyncio

    def write(version, with_artifact):
        m = _manifest().model_dump(mode="json", exclude_none=True)
        m["version"] = version
        d = tmp_path / "plugins" / "demo" / version / "linux"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(m))
        if with_artifact:
            (d / "artifact").mkdir()
            (d / "artifact" / "libx.so").write_bytes(b"x" + version.encode())

    write("1.0.0", True)
    write("1.1.0", False)                                      # forgot to build
    pk = tmp_path / "pk"
    pk.mkdir()

    async def run():
        reg = PluginRegistry(tmp_path / "plugins", LocalArtifactStore(pk), PluginPackager(pk))
        await reg.initialize()
        return [i.version for i in reg.list_versions("demo")]

    assert asyncio.run(run()) == ["1.0.0"]
