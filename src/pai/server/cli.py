"""Terminal client for the PAI plugin system.

Reads the same .env as the agent, logs in once, then runs one command.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
import argcomplete
import time
import httpx

from .config import DeviceConfig
from .transport import APIClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_device_id(config: DeviceConfig) -> Optional[str]:
    f = Path(config.state_dir) / "device_id"
    return f.read_text().strip() if f.exists() else None


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_table(
    rows: Sequence[Sequence[Any]], headers: Sequence[str]
) -> None:
    if not rows:
        print("(none)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

async def _cmd_list(args, client: APIClient) -> int:
    data = await client.list_plugins(
        user_id=client.user_id,
        platform=args.platform,
        architecture=args.architecture,
    )
    if args.json:
        _print_json(data)
        return 0
    plugins = data.get("plugins", [])
    rows = [
        (
            p.get("id"),
            p.get("version"),
            p.get("name"),
            ",".join(p.get("platforms") or []),
            ",".join(p.get("architectures") or []),
            ",".join(p.get("device_types") or []),
            "yes" if p.get("is_enabled") else "no",
            ",".join(p.get("capabilities") or [])[:60],
        )
        for p in plugins
    ]
    _print_table(rows, ["ID", "VERSION", "NAME","PLATFORMS", "ARCHITECTURES", "DEVICE_TYPES", "ENABLED", "CAPABILITIES"])
    print(
        f"\nTotal: {data.get('total', len(plugins))}  |  "
        f"Enabled: {data.get('enabled', 0)}"
    )
    return 0


async def _cmd_show(args, client: APIClient) -> int:
    _print_json(await client.get_plugin(args.plugin_id))
    return 0


def _parse_plugin_spec(spec: str) -> tuple[str, Optional[str]]:
    """Split 'browser@1.0.1' into ('browser', '1.0.1').

    rsplit so a hypothetical plugin id containing '@' still works —
    the version is always the last '@' segment.
    """
    if "@" in spec:
        pid, ver = spec.rsplit("@", 1)
        if pid and ver:
            return pid, ver
    return spec, None


async def _watch_install(
    client: APIClient,
    device_id: str,
    plugin_id: str,
    version: Optional[str] = None,
    target_shas: Optional[set[str]] = None,
    timeout: float = 600.0,
) -> int:
    """Poll until the device reports the target version + sha.

    `target_shas` is the set of every sha256 the backend advertises for
    this version — across all platforms. When the installed row's
    `artifact_sha256` is in that set, the correct artifact is on the
    device. We use a set rather than a single sha because the CLI
    doesn't know which platform the agent will pick (that depends on
    the agent's `_resolve_artifact` logic).
    """
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start = time.time()
    i = 0
    last_err: Optional[str] = None
    target = f"{plugin_id}@{version}" if version else plugin_id
    target_shas = target_shas or set()

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            print(f"\r✗ timed out after {timeout:.0f}s" + " " * 40)
            print(f"  Check:  server-cli status")
            return 1

        print(
            f"\r{spinner[i % len(spinner)]} "
            f"Installing {target}… {elapsed:5.1f}s",
            end="", flush=True,
        )
        i += 1

        try:
            data = await client.list_installed(device_id)
            entry = next(
                (p for p in data.get("plugins", [])
                 if p.get("plugin_id") == plugin_id),
                None,
            )
            if entry is not None:
                installed = entry.get("is_installed")
                installed_version = entry.get("version")
                installed_sha = entry.get("artifact_sha256")
                err = entry.get("last_error")
                if err == "install_requested":
                    err = None

                version_ok = not version or installed_version == version

                # sha check: only enforce when both sides have data.
                # If the backend doesn't expose artifact_sha256 yet
                # (older deploy), or the row predates sha reporting,
                # degrade to version-only — same as before.
                if target_shas and installed_sha is not None:
                    sha_ok = installed_sha in target_shas
                else:
                    sha_ok = True

                if installed and version_ok and sha_ok:
                    print(
                        f"\r✓ installed {plugin_id}@{installed_version} "
                        f"in {elapsed:.1f}s" + " " * 40
                    )
                    return 0

                if err and err != last_err:
                    print(f"\r✗ install failed: {err[:120]}" + " " * 40)
                    return 1
                last_err = err

        except Exception as exc:
            print(f"\r! poll error: {exc}" + " " * 40)

        await asyncio.sleep(1.0)


async def _cmd_install(args, client: APIClient, device_id: Optional[str]) -> int:
    if not device_id:
        print(
            "error: no device_id — run the agent once or pass --device",
            file=sys.stderr,
        )
        return 1

    pid, spec_version = _parse_plugin_spec(args.plugin_id)
    flag_version = getattr(args, "version", None)

    if spec_version and flag_version and spec_version != flag_version:
        print(
            f"error: version specified twice and they disagree "
            f"(@{spec_version} vs --version {flag_version})",
            file=sys.stderr,
        )
        return 2

    version = spec_version or flag_version
    force = getattr(args, "force", False)

    # --- Fetch target shas from the registry index --------------------
    # We do this regardless of --force so the watcher knows what sha to
    # wait for. Failure to fetch is non-fatal — the watcher degrades to
    # version-only.
    target_shas: set[str] = set()
    try:
        index = await client.fetch_index()
        idx_entry = next(
            (p for p in index.get("plugins", []) if p["id"] == pid),
            None,
        )
        versions = (idx_entry or {}).get("versions", {})
        vinfo = (
            versions.get(version) if version
            else versions.get((idx_entry or {}).get("latest"))
        ) or {}
        target_shas = {
            s for s in (vinfo.get("sha256") or {}).values() if s
        }
    except Exception as exc:
        print(
            f"  warning: could not fetch registry index: {exc}",
            file=sys.stderr,
        )

    # --- Pre-check ----------------------------------------------------
    # Skipped with --force. Compares installed state to target:
    #   - version mismatch      → reinstall (falls through)
    #   - version match, sha match → already up-to-date, exit
    #   - version match, sha diff  → reinstall (falls through)
    #   - version match, no sha    → assume up-to-date, exit with hint
    if version and not force:
        current = await client.list_installed(device_id)
        entry = next(
            (p for p in current.get("plugins", [])
             if p.get("plugin_id") == pid),
            None,
        )
        if (
            entry
            and entry.get("is_installed")
            and entry.get("version") == version
        ):
            local_sha = entry.get("artifact_sha256")

            if target_shas and local_sha is not None:
                if local_sha in target_shas:
                    print(
                        f"✓ {pid}@{version} is already installed "
                        f"and up-to-date on {device_id}",
                        file=sys.stderr,
                    )
                    return 0
                # sha drift at same version — fall through to install
                first = next(iter(target_shas))
                print(
                    f"⚠ {pid}@{version} installed but sha differs\n"
                    f"    local    = {local_sha[:16]}…\n"
                    f"    registry = {first[:16]}…\n"
                    f"  reinstalling",
                    file=sys.stderr,
                )
            else:
                reason = (
                    "installed sha not recorded"
                    if local_sha is None
                    else "no sha advertised in registry"
                )
                print(
                    f"✓ {pid}@{version} is already installed on "
                    f"{device_id}\n"
                    f"  ({reason}; use --force to reinstall)",
                    file=sys.stderr,
                )
                return 0

    # --- Request the install ------------------------------------------
    data = await client.install_plugin(pid, device_id, version=version)
    _print_json(data)

    if getattr(args, "watch", False) and not args.json:
        return await _watch_install(
            client, device_id, pid,
            version=version,
            target_shas=target_shas,
        )

    print(
        f"\nInstall requested: {pid}"
        + (f"@{version}" if version else " (latest)")
        + f"\nWatch it with:\n"
        f"  server-cli install {pid}"
        + (f"@{version}" if version else "")
        + " --watch\n"
        "or poll with:\n"
        "  server-cli status",
        file=sys.stderr,
    )
    return 0

async def _cmd_enable(args, client: APIClient, device_id: Optional[str]) -> int:
    data = await client.enable_plugin(args.plugin_id, device_id)
    _print_json(data)
    return 0


async def _cmd_disable(args, client: APIClient, device_id: Optional[str]) -> int:
    data = await client.disable_plugin(args.plugin_id, device_id)
    _print_json(data)
    return 0


async def _cmd_status(args, client: APIClient, device_id: Optional[str]) -> int:
    if not device_id:
        print(
            "error: no device_id — run the agent once or pass --device",
            file=sys.stderr,
        )
        return 1
    data = await client.list_installed(device_id)
    if args.json:
        _print_json(data)
        return 0
    plugins = data.get("plugins", [])
    rows = [
        (
            p.get("plugin_id"),
            p.get("version"),
            "yes" if p.get("is_installed") else "no",
            "yes" if p.get("enabled_on_device") else "no",
            (p.get("last_error") or "")[:40],
        )
        for p in plugins
    ]
    _print_table(
        rows, ["PLUGIN", "VERSION", "INSTALLED", "ENABLED", "LAST ERROR"]
    )
    return 0


async def _cmd_registry(args, client: APIClient) -> int:
    data = await client.fetch_index()
    if args.json:
        _print_json(data)
        return 0
    rows = []
    for p in data.get("plugins", []):
        rows.append((
            p.get("id"),
            p.get("name"),
            p.get("latest"),
            ", ".join((p.get("versions") or {}).keys()),
        ))
    _print_table(rows, ["ID", "NAME", "LATEST", "VERSIONS"])
    return 0


async def _cmd_catalog(args, client: APIClient) -> int:
    data = await client.get_catalog(args.platform, args.architecture)
    _print_json(data)
    return 0


async def _cmd_system(args, client: APIClient) -> int:
    _print_json(await client.plugin_system_status())
    return 0


async def _cmd_installed(args, client: APIClient, device_id: Optional[str]) -> int:
    if not device_id:
        print("error: no device_id — run the agent once or pass --device",
              file=sys.stderr)
        return 1
    data = await client.list_installed(device_id)
    if args.json:
        _print_json(data)
        return 0

    all_plugins = data.get("plugins", [])
    installed = [p for p in all_plugins if p.get("is_installed")]

    if not installed:
        print(f"No plugins installed on device {device_id}.")
        print(f"Available catalog:  server-cli list")
        return 0

    rows = [
        (
            p.get("plugin_id"),
            p.get("version") or "—",
            "yes" if p.get("enabled_on_device") else "no",
            (p.get("last_error") or "")[:40] if p.get("last_error") != "install_requested" else "",
        )
        for p in installed
    ]
    _print_table(
        rows,
        ["PLUGIN", "VERSION", "ENABLED", "LAST ERROR"],
    )
    print(f"\n{len(installed)} plugin(s) installed on {device_id}")
    return 0


async def _cmd_invoke(
    args, client: APIClient, device_id: Optional[str]
) -> int:
    if not device_id:
        print(
            "error: no device_id — run the agent once or pass --device",
            file=sys.stderr,
        )
        return 1

    # Parameters: --params '<json>'  |  --params @file.json  |  stdin
    raw = args.params
    if raw is None and not sys.stdin.isatty():
        raw = sys.stdin.read()

    if raw:
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text()
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"error: --params is not valid JSON: {exc}", file=sys.stderr)
            return 2
    else:
        params = {}

    try:
        resp = await client.invoke_capability(
            device_id=device_id,
            capability=args.capability,
            parameters=params,
            timeout=args.timeout,
        )
    except httpx.HTTPStatusError as exc:
        # Let _amain print the body; but for invoke we want a cleaner
        # message on 409 (device offline) and 504 (timeout).
        if exc.response.status_code in (409, 504):
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except Exception:
                detail = exc.response.text
            print(f"✗ {detail}", file=sys.stderr)
            return 1
        raise

    if args.json:
        _print_json(resp)
        return 0

    result = resp.get("result") or {}
    if isinstance(result, dict) and result.get("ok") is False:
        print(f"✗ {result.get('error', 'plugin reported failure')}",
              file=sys.stderr)
        if result.get("data") is not None:
            _print_json(result["data"])
        return 1

    # Success — print the data payload if there is one, else the whole result.
    data = result.get("data") if isinstance(result, dict) else result
    if data is None:
        data = result
    _print_json(data)
    return 0


async def _watch_uninstall(
    client: APIClient,
    device_id: str,
    plugin_id: str,
    timeout: float = 120.0,
) -> int:
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start = time.time()
    i = 0

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            print(f"\r✗ timed out after {timeout:.0f}s" + " " * 40)
            print(f"  Check:  server-cli status")
            return 1

        print(
            f"\r{spinner[i % len(spinner)]} "
            f"Uninstalling {plugin_id}… {elapsed:5.1f}s",
            end="", flush=True,
        )
        i += 1

        try:
            data = await client.list_installed(device_id)
            entry = next(
                (p for p in data.get("plugins", [])
                 if p.get("plugin_id") == plugin_id),
                None,
            )
            # Uninstalled = row gone, or row present with is_installed=False.
            if entry is None or not entry.get("is_installed"):
                print(f"\r✓ uninstalled {plugin_id} in {elapsed:.1f}s"
                      + " " * 40)
                return 0
        except Exception as exc:
            print(f"\r! poll error: {exc}" + " " * 40)

        await asyncio.sleep(1.0)


async def _cmd_uninstall(
    args, client: APIClient, device_id: Optional[str]
) -> int:
    if not device_id:
        print(
            "error: no device_id — run the agent once or pass --device",
            file=sys.stderr,
        )
        return 1

    data = await client.uninstall_plugin(args.plugin_id, device_id)
    _print_json(data)

    if getattr(args, "watch", False):
        return await _watch_uninstall(client, device_id, args.plugin_id)

    print(
        "\nUninstall requested. Watch it with:\n"
        f"  server-cli uninstall {args.plugin_id} --watch\n"
        "or poll with:\n"
        "  server-cli status",
        file=sys.stderr,
    )
    return 0

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def _dispatch(
    args, client: APIClient, device_id: Optional[str]
) -> int:
    cmd = args.command
    if cmd == "list":
        return await _cmd_list(args, client)
    if cmd == "show":
        return await _cmd_show(args, client)
    if cmd == "install":
        return await _cmd_install(args, client, device_id)
    if cmd == "uninstall":
        return await _cmd_uninstall(args, client, device_id)
    if cmd == "enable":
        return await _cmd_enable(args, client, device_id)
    if cmd == "disable":
        return await _cmd_disable(args, client, device_id)
    if cmd == "status":
        return await _cmd_status(args, client, device_id)
    if cmd == "registry":
        return await _cmd_registry(args, client)
    if cmd == "catalog":
        return await _cmd_catalog(args, client)
    if cmd == "system":
        return await _cmd_system(args, client)
    if cmd == "installed":
        return await _cmd_installed(args, client, device_id)
    if cmd == "invoke":
        return await _cmd_invoke(args, client, device_id)
    
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


async def _amain(args) -> int:
    try:
        config = DeviceConfig.from_env()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    client = APIClient(config.api_base, config.email, config.password)
    try:
        await client.login()
        device_id = args.device or _read_device_id(config)
        return await _dispatch(args, client, device_id)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        try:
            body = json.dumps(exc.response.json(), indent=2)
        except Exception:
            pass
        print(
            f"HTTP {exc.response.status_code} on "
            f"{exc.request.method} {exc.request.url}:\n{body}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    # Shared options that should be accepted after any subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--device",
        help="device_id (defaults to ~/.pai/state/device_id)",
    )
    common.add_argument(
        "--json", action="store_true", help="emit raw JSON",
    )

    p = argparse.ArgumentParser(
        prog="server-cli",
        description="Query and manage PAI plugins from the terminal.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "list", parents=[common],
        help="list plugins visible to this user",
    )
    sp.add_argument("--platform")
    sp.add_argument("--architecture")

    sp = sub.add_parser(
        "show", parents=[common], help="show one plugin's full metadata",
    )
    sp.add_argument("plugin_id")

    sp = sub.add_parser(
    "install", parents=[common],
    help="ask the device to install a plugin (async; poll status)",
    )
    sp.add_argument(
        "plugin_id",
        help="plugin id, optionally with @version: browser or browser@1.0.1",
    )
    sp.add_argument(
        "--version",
        help="pin a specific version (alternative to @version syntax)",
    )
    sp.add_argument(
        "--force", action="store_true",
        help="skip the 'already installed' pre-check and force a reinstall "
            "(needed when you've edited the artifact without bumping the version)",
    )
    sp.add_argument(
        "--watch", action="store_true",
        help="poll until the install completes (spinner + elapsed time)",
    )

    sp = sub.add_parser(
        "uninstall", parents=[common],
        help="ask the device to uninstall a plugin (async; poll status)",
    )
    sp.add_argument("plugin_id")
    sp.add_argument(
        "--watch", action="store_true",
        help="poll until the uninstall completes (spinner + elapsed time)",
    )

    sp = sub.add_parser(
        "enable", parents=[common],
        help="enable a plugin for this user (+ device if --device given)",
    )
    sp.add_argument("plugin_id")

    sp = sub.add_parser(
        "disable", parents=[common],
        help="disable a plugin for this user (+ device if --device given)",
    )
    sp.add_argument("plugin_id")

    sub.add_parser(
        "status", parents=[common],
        help="list plugins installed on the device",
    )

    sub.add_parser(
        "registry", parents=[common],
        help="show the backend's plugin registry index",
    )

    sp = sub.add_parser(
        "catalog", parents=[common],
        help="catalog of installable plugins for a platform",
    )
    sp.add_argument("--platform", default="linux")
    sp.add_argument("--architecture", default="x86_64")

    sub.add_parser(
        "system", parents=[common], help="plugin manager status",
    )

    sub.add_parser(
        "installed", parents=[common],
        help="list plugins actually installed on the device",
    )

    sp = sub.add_parser(
        "invoke", parents=[common],
        help="run a capability on the device and wait for the result",
    )
    sp.add_argument(
        "capability",
        help="capability id, e.g. browser.navigate or camera.capture",
    )
    sp.add_argument(
        "--params", "-p", default=None,
        help="JSON parameters; '@file.json' to read from file; omit to read stdin",
    )
    sp.add_argument(
        "--timeout", type=float, default=60.0,
        help="seconds to wait for the plugin result (default: 60)",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()                 # build once
    argcomplete.autocomplete(parser)         # arm completion on the same object
    args = parser.parse_args(argv)           # then parse
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())