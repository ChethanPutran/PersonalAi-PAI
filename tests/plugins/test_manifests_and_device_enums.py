"""Every shipped plugin manifest is valid, and legacy device names keep loading."""
import json
from pathlib import Path

import pytest

from pai.common.enums.device import DeviceArchitecture, DeviceType
from pai.plugins.manifest import PluginManifest

ROOT = Path(__file__).resolve().parents[2] / "plugin_registry" / "plugins"
MANIFESTS = sorted(ROOT.glob("*/*/*/manifest.json"))


def test_manifests_were_found():
    assert len(MANIFESTS) >= 7


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: "/".join(p.parts[-4:-1]))
def test_manifest_validates_and_its_artifact_exists(path):
    manifest = PluginManifest.from_dict(json.loads(path.read_text()))
    version_dir = path.parent.parent                       # <plugin>/<version>/
    for platform, spec in manifest.platforms.items():
        # Only <platform>/artifact/ is packaged, and its files land at <platform>/<file> in the
        # archive, so the manifest path "linux/libx.so" is the file linux/artifact/libx.so.
        parts = Path(spec.artifact).parts
        assert parts[0] == platform.value, f"{path}: artifact must live under '{platform.value}/'"
        source = version_dir / platform.value / "artifact" / Path(*parts[1:])
        assert source.is_file(), f"{path}: missing build output {source.relative_to(version_dir)}"


@pytest.mark.parametrize("raw,expected", [
    ("mobile", DeviceType.PHONE), ("MOBILE", DeviceType.PHONE), ("server", DeviceType.SERVER),
    ("phone", DeviceType.PHONE), ("notebook", DeviceType.LAPTOP),
])
def test_device_type_accepts_legacy_names(raw, expected):
    assert DeviceType(raw) is expected


@pytest.mark.parametrize("raw,expected", [
    ("arm64-v8a", DeviceArchitecture.ARM64), ("aarch64", DeviceArchitecture.ARM64),
    ("amd64", DeviceArchitecture.X86_64), ("x86_64", DeviceArchitecture.X86_64),
])
def test_architecture_accepts_toolchain_names(raw, expected):
    assert DeviceArchitecture(raw) is expected


def test_junk_values_are_still_rejected():
    with pytest.raises(ValueError):
        DeviceType("toaster")
    with pytest.raises(ValueError):
        DeviceArchitecture("riscv-banana")
