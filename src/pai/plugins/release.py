"""Package ONE plugin version for a GitHub release (`pai-package-plugin <id> <version>`).

Used by `.github/workflows/release-plugin.yml` after a tag such as `plugin/desktop/v1.2.0` is pushed. It builds
one deterministic `<id>-<version>-<platform>.tar.gz` per platform (the same packager the server uses, so the file is
byte-identical to what devices download), a `SHA256SUMS` file, and `RELEASE_NOTES.md` from the manifest changelog.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from pai.common.enums.device import DevicePlatform
from pai.plugins.manifest_install import load_manifest_file
from pai.plugins.packager import PluginPackager
from pai.plugins.versioning import Policy, PolicyError, SEMVER, find_root, load_policy


class ReleaseError(ValueError):
    pass


def parse_tag(policy: Policy, tag: str) -> tuple[str, str]:
    """`plugin/desktop/v1.2.0` -> ("desktop", "1.2.0") for the configured tag_format; anything else is an error."""
    head, _, tail = policy.tag_format.partition("{id}")
    mid, _, end = tail.partition("{version}")
    tag = tag.removeprefix("refs/tags/")
    if not (tag.startswith(head) and tag.endswith(end) if end else tag.startswith(head)):
        raise ReleaseError(f"tag {tag!r} does not match tag_format {policy.tag_format!r}")
    body = tag[len(head): len(tag) - len(end)] if end else tag[len(head):]
    plugin_id, sep, version = body.partition(mid)
    if not sep or not plugin_id or not SEMVER.match(version):
        raise ReleaseError(f"tag {tag!r} does not match tag_format {policy.tag_format!r} (id + X.Y.Z)")
    return plugin_id, version


def release_notes(plugin_id: str, version: str, manifests: Sequence[dict]) -> str:
    cl = next((m.get("changelog") for m in manifests if m.get("changelog")), None) or {}
    lines = [f"# {plugin_id} {version}", ""]
    if cl.get("summary"):
        lines += [str(cl["summary"]), ""]
    for key, title in (("breaking", "Breaking changes"), ("features", "New"), ("fixes", "Fixes")):
        if cl.get(key):
            lines += [f"## {title}", *[f"- {item}" for item in cl[key]], ""]
    if len(lines) == 2:
        lines += ["No changelog was provided for this version.", ""]
    rt = (manifests[0].get("runtime") or {}) if manifests else {}
    needs = [f"PAI runtime ≥ {rt['minPaiVersion']}" if rt.get("minPaiVersion") else "", f"PAI app ≥ {rt['minAppVersion']}" if rt.get("minAppVersion") else ""]
    if any(needs):
        lines += ["## Requires", *[f"- {n}" for n in needs if n], ""]
    return "\n".join(lines)


def package_release(root: Path, policy: Policy, plugin_id: str, version: str, out: Path) -> List[Path]:
    version_dir = root / policy.registry / plugin_id / version
    if not version_dir.is_dir():
        raise ReleaseError(f"{version_dir.relative_to(root)} does not exist: the tag must point at a commit that contains this version")
    out.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []
    raw: List[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        packager = PluginPackager(Path(tmp))
        for platform_dir in sorted(p for p in version_dir.iterdir() if p.is_dir()):
            mf = platform_dir / policy.manifest_name
            if not mf.is_file():
                continue
            manifest = load_manifest_file(mf)
            raw.append(json.loads(mf.read_text(encoding="utf-8")))
            if manifest.version != version:
                raise ReleaseError(f"{mf.relative_to(root)} says version {manifest.version}, the tag says {version}")
            platform = DevicePlatform(platform_dir.name)
            artifact = packager.package(manifest, version_dir, platform)
            target = out / f"{plugin_id}-{version}-{platform.value}.tar.gz"
            shutil.copyfile(artifact.packagePath, target)
            produced.append(target)
    if not produced:
        raise ReleaseError(f"no platform with a {policy.manifest_name} under {version_dir.relative_to(root)}")
    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in produced), encoding="utf-8")
    notes = out / "RELEASE_NOTES.md"
    notes.write_text(release_notes(plugin_id, version, raw), encoding="utf-8")
    return [*produced, sums, notes]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="pai-package-plugin", description=__doc__)
    ap.add_argument("plugin", help="plugin folder id, e.g. desktop — or a release tag like plugin/desktop/v1.2.0")
    ap.add_argument("version", nargs="?", help="X.Y.Z (omit when the first argument is a tag)")
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    a = ap.parse_args(argv)
    try:
        root = find_root()
        policy = load_policy(root)
        plugin_id, version = (a.plugin, a.version) if a.version else parse_tag(policy, a.plugin)
        files = package_release(root, policy, plugin_id, version, Path(a.out))
    except (PolicyError, ReleaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"packaged {plugin_id} {version} (tag {policy.tag_for(plugin_id, version)}):")
    for f in files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
