"""The backend ships a plugin as <platform>/<files> in a .tar.gz; the device must unpack it, safely."""
import io
import tarfile
from pathlib import Path

import pytest

from pai_device.plugins import DevicePluginManager

MANIFEST = {"id": "files", "platforms": {"linux": {"artifact": "linux/libx.so", "entrypoint": "x"}}}


def make_pkg(path: Path, files: dict, extra=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info)
    return path


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    m = DevicePluginManager(tmp_path / "plugins")

    async def no_start(self, plugin_id):        # loading a real .so is not what these tests are about
        return None
    monkeypatch.setattr(DevicePluginManager, "start", no_start)
    return m


@pytest.mark.asyncio
async def test_a_package_is_unpacked_and_the_manifests_artifact_is_used(mgr, tmp_path):
    pkg = make_pkg(tmp_path / "dl" / "files-1.0.0.tar.gz", {"linux/libx.so": b"\x7fELF-real", "linux/data.bin": b"d"})
    plugin = await mgr.install("files", "1.0.0", MANIFEST, pkg, "abc")
    assert plugin.artifact == mgr.plugin_root / "files" / "1.0.0" / "linux" / "libx.so"
    assert plugin.artifact.read_bytes() == b"\x7fELF-real"            # the library, not the gzip archive
    assert (plugin.dir / "linux" / "data.bin").exists() and not pkg.exists()
    assert (plugin.dir / "manifest.json").exists() and (plugin.dir / "sha256.txt").read_text() == "abc"


@pytest.mark.asyncio
async def test_a_bare_artifact_is_still_just_copied(mgr, tmp_path):
    raw = tmp_path / "dl" / "libx.so"
    raw.parent.mkdir()
    raw.write_bytes(b"\x7fELF-raw")
    plugin = await mgr.install("files", "1.0.0", {"id": "files"}, raw, None)
    assert plugin.artifact.read_bytes() == b"\x7fELF-raw" and plugin.artifact.name == "libx.so"


@pytest.mark.asyncio
async def test_unsafe_or_empty_packages_are_refused(mgr, tmp_path):
    evil = make_pkg(tmp_path / "evil.tar.gz", {"../escape.so": b"x"})
    with pytest.raises(RuntimeError, match="unsafe path"):
        await mgr.install("files", "1.0.0", MANIFEST, evil, None)
    assert not (tmp_path / "plugins" / "escape.so").exists() and not (tmp_path / "escape.so").exists()

    link = tarfile.TarInfo("linux/libx.so")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    with pytest.raises(RuntimeError, match="unsupported entry"):
        await mgr.install("files", "1.0.1", MANIFEST, make_pkg(tmp_path / "link.tar.gz", {}, [link]), None)

    with pytest.raises(RuntimeError, match="no artifact found"):
        await mgr.install("files", "1.0.2", MANIFEST, make_pkg(tmp_path / "empty.tar.gz", {"README": b"x"}), None)


@pytest.mark.asyncio
async def test_an_unpacked_install_survives_a_restart(mgr, tmp_path):
    await mgr.install("files", "1.0.0", MANIFEST, make_pkg(tmp_path / "p.tar.gz", {"linux/libx.so": b"\x7fELF"}), "abc")
    fresh = DevicePluginManager(tmp_path / "plugins")
    await fresh.load_existing()
    assert [p.id for p in fresh.list_all()] == ["files"]
    assert fresh.list_all()[0].artifact.name == "libx.so"
