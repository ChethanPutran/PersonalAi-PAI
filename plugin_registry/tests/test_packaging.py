"""Guarantee every published plugin has a real, deterministic package.

If this test passes, `PluginManager._on_initialize`'s check
    assert all(p.checksum and p.size > 0 for p in catalog.values())
can never fail at startup.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pai.common.enums.device import DevicePlatform
from pai.plugins.manifest_install import load_manifest_file
from pai.plugins.packager import PluginPackager


REGISTRY = Path(__file__).parent.parent / "plugins"


def _all_manifests() -> list[Path]:
    return sorted(REGISTRY.glob("**/manifest.json"))


@pytest.mark.parametrize(
    "manifest_path",
    _all_manifests(),
    ids=lambda p: str(p.relative_to(REGISTRY)),
)
def test_manifest_loads(manifest_path: Path) -> None:
    """Every manifest.json in the repo must pass both validation layers."""
    m = load_manifest_file(manifest_path)
    assert m.id
    assert m.capabilities
    assert m.author
    assert m.license


@pytest.mark.parametrize(
    "manifest_path",
    _all_manifests(),
    ids=lambda p: str(p.relative_to(REGISTRY)),
)
def test_package_is_deterministic(
    manifest_path: Path, tmp_path: Path,
) -> None:
    """Packaging the same directory twice must yield the same sha256."""
    m = load_manifest_file(manifest_path)

    # The packager wants the *plugin source dir*: for a single-manifest
    # layout that's the manifest's parent; for a per-platform layout it's
    # the version dir (the manifest's grandparent when the dir is named
    # after a platform).
    parent = manifest_path.parent
    if parent.name in {"linux", "android", "macos", "ios", "windows"}:
        source = parent.parent
    else:
        source = parent

    platform = DevicePlatform(parent.name)
    packager = PluginPackager(tmp_path)

    a1 = packager.package(m, source, platform)
    assert a1.checksum.startswith("sha256:")
    assert a1.size > 0

    # Repackage into a fresh directory → must be byte-identical.
    tmp2 = tmp_path / "run2"
    a2 = PluginPackager(tmp2).package(m, source, platform)
    assert a2.checksum == a1.checksum, (
        f"packaging is not deterministic for {m.id}@{m.version}: "
        f"{a1.checksum} != {a2.checksum}"
    )
    assert a2.size == a1.size
