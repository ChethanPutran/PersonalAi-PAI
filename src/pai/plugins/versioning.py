"""Version control for the runtime, the manifest schema, the app and every plugin.

Everything is configured in ONE place, `pyproject.toml`:

    [tool.pai.versions]   runtime / manifest schema range / app versions (the single source of truth)
    [tool.pai.plugins]    where plugins live and the rules a plugin release must follow

and enforced by two commands, run locally and in GitHub Actions (`.github/workflows/versions.yml`):

    pai-sync-versions [--write]         the constants the code/app actually use match pyproject
                                        (`pai/version.py`, `[project].version`, the manifest JSON schema,
                                        `frontend/pubspec.yaml`, `frontend/lib/core/app_version.dart`)
    pai-check-plugin-versions [--base]  every plugin manifest is well formed for those versions, and a
                                        RELEASED plugin version is never changed (publish a new version)

Rules for plugins (all in `[tool.pai.plugins]`): a version is `X.Y.Z` and lives in
`<registry>/<id>/<version>/<platform>/` (folder name == manifest `version`; manifest `id` is the folder name, or `<folder>_<platform>`); a manifest
may not require a runtime/app newer than this repository's; a version that exists on the base branch is
immutable (any file change = bump the version); a new version must be higher than every existing one and, for an
update to an existing plugin, carry a `changelog.summary`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from packaging.version import InvalidVersion, Version

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# ═══════════════════════════════════════════════════════════════════ policy (pyproject.toml)

@dataclass(frozen=True)
class Policy:
    runtime: str
    manifest_schema_min: int
    manifest_schema_max: int
    min_supported_app: str
    app: str
    registry: str = "plugin_registry/plugins"
    manifest_name: str = "manifest.json"
    tag_format: str = "plugin/{id}/v{version}"
    immutable_released_versions: bool = True
    require_changelog_on_update: bool = True
    base_ref: str = "origin/main"

    def tag_for(self, plugin_id: str, version: str) -> str:
        return self.tag_format.format(id=plugin_id, version=version)


class PolicyError(ValueError):
    """pyproject.toml is missing or has an invalid [tool.pai] section."""


def find_root(start: Optional[Path] = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").is_file():
            return cand
    raise PolicyError("pyproject.toml not found (run from inside the repository)")


def load_policy(root: Path) -> Policy:
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"cannot read pyproject.toml: {exc}") from exc
    pai = data.get("tool", {}).get("pai", {})
    v, pl = pai.get("versions"), pai.get("plugins", {})
    if not isinstance(v, dict):
        raise PolicyError("pyproject.toml has no [tool.pai.versions] section")
    missing = [k for k in ("runtime", "manifest_schema_min", "manifest_schema_max", "min_supported_app", "app") if k not in v]
    if missing:
        raise PolicyError(f"[tool.pai.versions] is missing: {', '.join(missing)}")
    for key in ("runtime", "min_supported_app", "app"):
        if not SEMVER.match(str(v[key])):
            raise PolicyError(f"[tool.pai.versions] {key} = {v[key]!r} is not X.Y.Z")
    lo, hi = int(v["manifest_schema_min"]), int(v["manifest_schema_max"])
    if not 1 <= lo <= hi:
        raise PolicyError(f"manifest schema range {lo}..{hi} is invalid")
    if Version(v["min_supported_app"]) > Version(v["app"]):
        raise PolicyError("min_supported_app is newer than app")
    known = {f for f in Policy.__dataclass_fields__ if f not in ("runtime", "manifest_schema_min", "manifest_schema_max", "min_supported_app", "app")}
    unknown = set(pl) - known
    if unknown:
        raise PolicyError(f"[tool.pai.plugins] has unknown keys: {', '.join(sorted(unknown))}")
    return Policy(runtime=str(v["runtime"]), manifest_schema_min=lo, manifest_schema_max=hi,
                  min_supported_app=str(v["min_supported_app"]), app=str(v["app"]), **pl)


# ═══════════════════════════════════════════════════════════════════ sync (code/app follow pyproject)

@dataclass(frozen=True)
class Drift:
    file: str
    what: str
    expected: str
    found: Optional[str]

    def __str__(self) -> str:
        return f"{self.file}: {self.what} is {self.found!r}, pyproject.toml says {self.expected!r}"


@dataclass(frozen=True)
class _Target:
    file: str
    what: str
    pattern: re.Pattern
    render: str          # replacement text; "{v}" is the expected value
    expected: str


def _targets(p: Policy) -> List[_Target]:
    def py(name: str, value: object) -> _Target:
        return _Target("src/pai/version.py", name, re.compile(rf'^({name}\s*=\s*)(.+)$', re.M), rf'\g<1>{value!r}' if isinstance(value, int) else rf'\g<1>"{value}"', str(value))

    return [
        py("RUNTIME_VERSION", p.runtime),
        py("MANIFEST_SCHEMA_MIN", p.manifest_schema_min),
        py("MANIFEST_SCHEMA_MAX", p.manifest_schema_max),
        py("MIN_SUPPORTED_APP_VERSION", p.min_supported_app),
        # [project].version = the runtime version (the first `version = "..."` line, which is under [project])
        _Target("pyproject.toml", "[project].version", re.compile(r'^(version\s*=\s*)"([^"]*)"', re.M), rf'\g<1>"{p.runtime}"', p.runtime),
        _Target("src/pai/plugins/manifest.schema.json", "schemaVersion minimum",
                re.compile(r'("schemaVersion":\s*\{\s*"type":\s*"integer",\s*"minimum":\s*)(\d+)'), rf"\g<1>{p.manifest_schema_min}", str(p.manifest_schema_min)),
        _Target("src/pai/plugins/manifest.schema.json", "schemaVersion maximum",
                re.compile(r'("schemaVersion":\s*\{[^}]*?"maximum":\s*)(\d+)'), rf"\g<1>{p.manifest_schema_max}", str(p.manifest_schema_max)),
        _Target("frontend/pubspec.yaml", "version (before +build)", re.compile(r'^(version:\s*)(\d+\.\d+\.\d+)(\+\d+)?$', re.M), rf"\g<1>{p.app}\g<3>", p.app),
        _Target("frontend/lib/core/app_version.dart", "kAppVersion", re.compile(r"(const String kAppVersion = ')([^']*)(';)"), rf"\g<1>{p.app}\g<3>", p.app),
    ]


def _found(t: _Target, text: str) -> Optional[str]:
    m = t.pattern.search(text)
    if not m:
        return None
    return m.group(2).strip().strip("\"'")


def sync(policy: Policy, root: Path, *, write: bool = False) -> List[Drift]:
    """Compare (and with write=True, fix) every place the versions are copied to. Returns what was off."""
    drift: List[Drift] = []
    schema_path = root / "src/pai/plugins/manifest.schema.json"
    if schema_path.exists() and '"maximum"' not in schema_path.read_text(encoding="utf-8").split('"schemaVersion"', 1)[-1][:200] and write:
        text = schema_path.read_text(encoding="utf-8")
        text = re.sub(r'("schemaVersion":\s*\{\s*"type":\s*"integer",\s*"minimum":\s*\d+)(\s*\})', r'\1, "maximum": 0\2', text, count=1)
        schema_path.write_text(text, encoding="utf-8")
    by_file: Dict[str, List[_Target]] = {}
    for t in _targets(policy):
        by_file.setdefault(t.file, []).append(t)
    for rel, targets in by_file.items():
        path = root / rel
        if not path.exists():
            drift.append(Drift(rel, "file", "exists", None))
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for t in targets:
            found = _found(t, new)
            if found != t.expected:
                drift.append(Drift(rel, t.what, t.expected, found))
                if write and t.pattern.search(new):
                    new = t.pattern.sub(t.render, new, count=1)
        if write and new != text:
            path.write_text(new, encoding="utf-8")
    return drift


# ═══════════════════════════════════════════════════════════════════ manifests

@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.path}: {self.message}"


def _v(value: object) -> Optional[Version]:
    try:
        return Version(str(value))
    except InvalidVersion:
        return None


def manifest_paths(policy: Policy, root: Path) -> List[Path]:
    return sorted((root / policy.registry).glob(f"*/*/*/{policy.manifest_name}"))


def check_manifests(policy: Policy, root: Path) -> List[Violation]:
    """Every manifest is consistent with its folder and with this repository's runtime/app/schema versions."""
    out: List[Violation] = []
    reg = root / policy.registry
    seen: Dict[Tuple[str, str], Set[str]] = {}
    for path in manifest_paths(policy, root):
        rel = path.relative_to(root).as_posix()
        plugin_id, folder_ver, platform = path.relative_to(reg).parts[:3]
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append(Violation("manifest-json", rel, f"not valid JSON: {exc}"))
            continue
        if not SEMVER.match(folder_ver):
            out.append(Violation("version-format", rel, f"folder {folder_ver!r} is not X.Y.Z"))
        if m.get("id") not in (plugin_id, f"{plugin_id}_{platform}"):       # `desktop`, or `camera_linux` for a per-platform id
            out.append(Violation("id-matches-folder", rel, f'manifest id {m.get("id")!r} is neither {plugin_id!r} nor '
                                 f'{plugin_id + "_" + platform!r} (folder <id>/<version>/<platform>)'))
        ver = m.get("version")
        if ver != folder_ver:
            out.append(Violation("version-matches-folder", rel, f"manifest version {ver!r} != folder {folder_ver!r}"))
        elif not SEMVER.match(str(ver)):
            out.append(Violation("version-format", rel, f"version {ver!r} is not X.Y.Z"))
        sv = m.get("schemaVersion")
        if not isinstance(sv, int) or not policy.manifest_schema_min <= sv <= policy.manifest_schema_max:
            out.append(Violation("schema-version", rel, f"schemaVersion {sv!r} is outside the supported range "
                                 f"{policy.manifest_schema_min}..{policy.manifest_schema_max}"))
        rt = m.get("runtime") or {}
        for key, limit, name in (("minPaiVersion", policy.runtime, "runtime"), ("minAppVersion", policy.app, "app")):
            need = rt.get(key)
            if need is None:
                continue
            nv = _v(need)
            if nv is None:
                out.append(Violation(f"{key}-format", rel, f"{key} {need!r} is not a version"))
            elif nv > Version(limit):
                out.append(Violation(f"{key}-not-released", rel, f"requires {name} {need}, newer than this repository's {name} {limit}"))
        seen.setdefault((plugin_id, folder_ver), set()).add(str(ver))
    return out


# ═══════════════════════════════════════════════════════════════════ released versions are immutable

class GitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def _base_commit(root: Path, base_ref: str) -> str:
    try:
        return _git(root, "merge-base", base_ref, "HEAD").strip()
    except GitError:
        _git(root, "rev-parse", "--verify", base_ref)      # raises a clearer error if the ref itself is missing
        return _git(root, "rev-parse", base_ref).strip()


def _versions_at(root: Path, ref: str, registry: str) -> Dict[str, Set[str]]:
    """{plugin_id: {versions}} present in the registry at a git ref."""
    out: Dict[str, Set[str]] = {}
    for line in _git(root, "ls-tree", "-r", "--name-only", ref, "--", registry).splitlines():
        parts = Path(line).relative_to(registry).parts
        if len(parts) >= 3:
            out.setdefault(parts[0], set()).add(parts[1])
    return out


def _versions_on_disk(root: Path, registry: str) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    reg = root / registry
    for p in sorted(reg.glob("*/*")):
        if p.is_dir() and any(p.rglob("*")):
            out.setdefault(p.parent.name, set()).add(p.name)
    return out


def _is_manifest_file(policy: Policy, path: str) -> bool:
    return Path(path).name == policy.manifest_name


def check_released(policy: Policy, root: Path, base_ref: Optional[str] = None) -> List[Violation]:
    """Against the base branch: released versions unchanged; new versions higher and with a changelog."""
    ref = base_ref or policy.base_ref
    base = _base_commit(root, ref)
    reg = policy.registry.rstrip("/")
    in_base = _versions_at(root, base, reg)
    out: List[Violation] = []

    if policy.immutable_released_versions:
        changes = [tuple(line.partition("\t")[::2]) for line in
                   _git(root, "diff", "--name-status", "--no-renames", base, "--", reg).splitlines()]
        # `git diff` does not list files that were never `git add`ed: a new file dropped into a released version
        changes += [("A", path) for path in _git(root, "ls-files", "--others", "--exclude-standard", "--", reg).splitlines()]
        for status, path in changes:
            parts = Path(path).relative_to(reg).parts
            if len(parts) < 3:
                continue
            plugin_id, version = parts[0], parts[1]
            if version in in_base.get(plugin_id, set()):
                verb = {"A": "added to", "M": "modified in", "D": "deleted from", "T": "retyped in"}.get(status[:1], "changed in")
                out.append(Violation("released-version-immutable", path,
                                     f"file {verb} {plugin_id} {version}, which already exists on {ref}. "
                                     f"A released version never changes: publish a new version instead "
                                     f"(python scripts/bump_plugin.py {plugin_id} patch)."))
        for pid, versions in in_base.items():                      # a whole released version removed
            now = _versions_on_disk(root, reg).get(pid, set())
            for gone in sorted(versions - now):
                out.append(Violation("released-version-immutable", f"{reg}/{pid}/{gone}", f"released version {gone} of {pid} was removed"))

    on_disk = _versions_on_disk(root, reg)
    for pid, versions in sorted(on_disk.items()):
        old = in_base.get(pid, set())
        for ver in sorted(versions - old, key=lambda s: _v(s) or Version("0")):
            where = f"{reg}/{pid}/{ver}"
            nv = _v(ver)
            if nv is None or not SEMVER.match(ver):
                continue                                           # reported by check_manifests
            highest = max((_v(o) for o in old if _v(o)), default=None)
            if highest is not None and nv <= highest:
                out.append(Violation("new-version-higher", where, f"new version {ver} is not higher than existing {highest} of {pid}"))
            if old and policy.require_changelog_on_update:
                for mf in sorted((root / where).glob(f"*/{policy.manifest_name}")):
                    try:
                        cl = (json.loads(mf.read_text(encoding="utf-8")).get("changelog") or {})
                    except (OSError, ValueError):
                        continue
                    if not str(cl.get("summary", "")).strip():
                        out.append(Violation("changelog-required", mf.relative_to(root).as_posix(),
                                             f'{pid} {ver} updates an existing plugin: add "changelog": {{"summary": "..."}}'))
    return out


# ═══════════════════════════════════════════════════════════════════ CLIs

def _emit(items: Iterable[object], github: bool, title: str) -> None:
    for it in items:
        if github:
            file = getattr(it, "path", None) or getattr(it, "file", "")
            print(f"::error file={file},title={title}::{it}")
        else:
            print(f"  ✖ {it}")


def sync_main(argv: Optional[Sequence[str]] = None) -> int:
    """`pai-sync-versions`: make the copies of the versions match [tool.pai.versions] (or just check)."""
    ap = argparse.ArgumentParser(prog="pai-sync-versions", description=sync_main.__doc__)
    ap.add_argument("--write", action="store_true", help="rewrite the out-of-date files (default: only check)")
    ap.add_argument("--github", action="store_true", help="print GitHub Actions error annotations")
    a = ap.parse_args(argv)
    try:
        root = find_root()
        policy = load_policy(root)
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    drift = sync(policy, root, write=a.write)
    if not drift:
        print(f"versions in sync: runtime {policy.runtime}, manifest schema {policy.manifest_schema_min}-{policy.manifest_schema_max}, "
              f"app {policy.app} (min app {policy.min_supported_app})")
        return 0
    if a.write:
        print(f"updated {len({d.file for d in drift})} file(s) to match pyproject.toml:")
        for d in drift:
            print(f"  ✔ {d}")
        return 0
    print("versions are out of sync with pyproject.toml [tool.pai.versions]:")
    _emit(drift, a.github, "version drift")
    print("Fix: edit [tool.pai.versions] in pyproject.toml, then run `pai-sync-versions --write`.")
    return 1


def check_main(argv: Optional[Sequence[str]] = None) -> int:
    """`pai-check-plugin-versions`: manifests match the versions in pyproject; released versions are unchanged."""
    ap = argparse.ArgumentParser(prog="pai-check-plugin-versions", description=check_main.__doc__)
    ap.add_argument("--base", default=None, help="git ref of the released state (default: [tool.pai.plugins] base_ref)")
    ap.add_argument("--no-git", action="store_true", help="only check manifests (no comparison with a base branch)")
    ap.add_argument("--github", action="store_true", help="print GitHub Actions error annotations")
    a = ap.parse_args(argv)
    try:
        root = find_root()
        policy = load_policy(root)
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problems: List[Violation] = check_manifests(policy, root)
    if not a.no_git:
        try:
            problems += check_released(policy, root, a.base)
        except GitError as exc:
            print(f"error: cannot compare with {a.base or policy.base_ref}: {exc}\n"
                  f"(in CI use `fetch-depth: 0`; locally `git fetch origin`; or pass --no-git)", file=sys.stderr)
            return 2
    n = len(manifest_paths(policy, root))
    if not problems:
        print(f"plugin versions OK: {n} manifest(s)" + ("" if a.no_git else f", released versions unchanged vs {a.base or policy.base_ref}"))
        return 0
    print(f"{len(problems)} plugin version problem(s):")
    _emit(problems, a.github, "plugin version")
    return 1


if __name__ == "__main__":                                         # python -m pai.plugins.versioning <sync|check> ...
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    raise SystemExit(sync_main(rest) if cmd == "sync" else check_main(rest) if cmd == "check" else
                     print("usage: python -m pai.plugins.versioning <sync|check> [options]") or 2)
