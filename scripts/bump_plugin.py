#!/usr/bin/env python3
"""Bump a PAI plugin version.

Usage:
    python scripts/bump_plugin.py browser patch
    python scripts/bump_plugin.py browser minor --dry-run
    python scripts/bump_plugin.py browser 2.0.0
    python scripts/bump_plugin.py browser --list

The version lives in two places (directory name + manifest.version).
This script keeps them in sync and preserves the old version on disk
so devices can pin/roll back via `/artifact/{id}/{old_version}/...`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_semver(v: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(v)
    if not m:
        raise ValueError(f"not semver: {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump(v: str, kind: str) -> str:
    major, minor, patch = parse_semver(v)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown kind: {kind!r}")


def find_current(plugin_dir: Path) -> tuple[str, Path]:
    """Return (highest_version, its_directory) under plugin_dir."""
    versions = []
    for d in plugin_dir.iterdir():
        if d.is_dir() and SEMVER_RE.match(d.name):
            versions.append((d.name, d))
    if not versions:
        raise FileNotFoundError(
            f"No semver directories under {plugin_dir}"
        )
    versions.sort(key=lambda t: parse_semver(t[0]), reverse=True)
    return versions[0][0], versions[0][1]


def reload_backend() -> int:
    """
    Ask the backend to re-read the plugin_registry directory.

    Requires:
      - PAI_BACKEND_URL   e.g. http://localhost:8000
      - PAI_ADMIN_TOKEN   a JWT with is_admin=true
                         (or fall back to PAI_EMAIL + PAI_PASSWORD)

    Returns the HTTP status code, or -1 on connection error.
    """
    try:
        import httpx
    except ImportError:
        print(
            "  reload skipped: httpx not installed",
            file=sys.stderr,
        )
        return -1

    base = os.environ.get("PAI_BACKEND_URL")
    if not base:
        print(
            "  reload skipped: PAI_BACKEND_URL not set",
            file=sys.stderr,
        )
        return -1

    token = os.environ.get("PAI_ADMIN_TOKEN")
    if not token:
        # Fall back to a login round-trip using agent credentials.
        email = os.environ.get("PAI_EMAIL") or os.environ.get("PAI_USERNAME")
        password = os.environ.get("PAI_PASSWORD")
        if not email or not password:
            print(
                "  reload skipped: set PAI_ADMIN_TOKEN, or "
                "PAI_EMAIL + PAI_PASSWORD",
                file=sys.stderr,
            )
            return -1
        try:
            login = httpx.post(
                f"{base.rstrip('/')}/api/v1/auth/login",
                json={"email": email, "password": password},
                timeout=10,
            )
            login.raise_for_status()
            body = login.json()
            if not body.get("is_admin"):
                print(
                    "  reload skipped: login user is not admin",
                    file=sys.stderr,
                )
                return -1
            token = body["access_token"]
        except httpx.HTTPError as exc:
            print(f"  reload failed: login error: {exc}", file=sys.stderr)
            return -1

    try:
        r = httpx.post(
            f"{base.rstrip('/')}/api/v1/plugins/system/reload",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        print(f"  reload failed: {exc}", file=sys.stderr)
        return -1

    if r.is_success:
        try:
            count = r.json().get("plugin_count", "?")
        except Exception:
            count = "?"
        print(f"  backend reloaded ({r.status_code}, {count} plugins)")
    else:
        print(
            f"  reload failed: HTTP {r.status_code}: {r.text[:200]}",
            file=sys.stderr,
        )
    return r.status_code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("plugin", nargs="?", help="plugin id, e.g. browser")
    p.add_argument(
        "target", nargs="?",
        help="patch|minor|major|<explicit-version>",
    )
    p.add_argument(
        "--registry", default="plugin_registry",
        help="plugin registry root (default: ./plugin_registry)",
    )
    p.add_argument(
        "--delete-old", action="store_true",
        help="remove the previous version directory after bump",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="show what would happen; no writes",
    )
    p.add_argument(
        "--commit", action="store_true",
        help="git add + commit the bump (requires git repo)",
    )
    p.add_argument(
        "--reload-backend", action="store_true",
        help="call POST /plugins/system/reload after bumping "
             "(needs PAI_BACKEND_URL + PAI_ADMIN_TOKEN, or "
             "PAI_EMAIL + PAI_PASSWORD)",
    )
    p.add_argument(
        "--list", action="store_true",
        help="list every plugin and its current version",
    )
    args = p.parse_args()

    registry = Path(args.registry).resolve()
    if not registry.is_dir():
        print(f"error: registry not found: {registry}", file=sys.stderr)
        return 1

    # --list: dump every plugin's current version
    if args.list:
        for plugin_dir in sorted(registry.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue
            try:
                v, _ = find_current(plugin_dir)
                print(f"{plugin_dir.name:<20} {v}")
            except FileNotFoundError:
                print(f"{plugin_dir.name:<20} (no versions)")
        return 0

    if not args.plugin or not args.target:
        p.error("plugin and target are required (or use --list)")

    plugin_dir = registry / args.plugin
    if not plugin_dir.is_dir():
        print(f"error: plugin not found: {plugin_dir}", file=sys.stderr)
        return 1

    try:
        current_version, current_dir = find_current(plugin_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Resolve target → new version string
    if args.target in ("patch", "minor", "major"):
        new_version = bump(current_version, args.target)
    else:
        try:
            parse_semver(args.target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        new_version = args.target

    if parse_semver(new_version) <= parse_semver(current_version):
        print(
            f"error: new version {new_version} is not greater than "
            f"current {current_version}", file=sys.stderr,
        )
        return 1

    new_dir = plugin_dir / new_version
    if new_dir.exists():
        print(f"error: already exists: {new_dir}", file=sys.stderr)
        return 1

    print(f"  plugin : {args.plugin}")
    print(f"  current: {current_version}  ({current_dir.name}/)")
    print(f"  new    : {new_version}  ({new_version}/)")

    if args.dry_run:
        print("\n[dry-run] no changes written")
        return 0

    # 1. Copy the tree (whole version dir, all platforms' artifacts)
    shutil.copytree(current_dir, new_dir)

    # 2. Update manifest.version
    manifest_path = new_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"error: manifest missing in {new_dir}", file=sys.stderr)
        shutil.rmtree(new_dir)
        return 1
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = new_version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    # 3. Optionally remove the old version dir
    if args.delete_old:
        shutil.rmtree(current_dir)
        print(f"  removed: {current_dir.name}/")

    print(f"\n✓ {args.plugin}  {current_version} → {new_version}")

    # 4. Optionally commit
    if args.commit:
        msg = (
            f"{args.plugin}: {current_version} → {new_version}\n"
            f"\n"
            f"Bumped plugin registry version."
        )
        subprocess.run(
            ["git", "add", "-A", str(new_dir.parent)],
            check=True,
        )
        subprocess.run(["git", "commit", "-m", msg], check=True)
        print("  committed")

    # 5. Optionally reload the backend's catalog cache
    if args.reload_backend:
        rc = reload_backend()
        if rc < 0 or rc >= 400:
            print(
                "  note: backend reload did not succeed; "
                "the device will still pick up the new version via "
                "/index.json on its next _sync_once (≤60s)",
                file=sys.stderr,
            )

    print(f"\nnext steps:")
    print(f"  server-cli install {args.plugin} --watch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())