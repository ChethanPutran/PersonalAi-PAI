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

from . import chatcli, cli_settings, voicecli
from .cli_support import (
    Ctx, dim, error_text, follow_operation, parse_params, print_confirmation, print_json as _print_json,
    print_table as _print_table, print_task_outcome, wait_for_task,
)
from .config import DeviceConfig
from .transport import APIClient

# Commands that live in their own modules: name -> async handler(args, ctx)
MODULE_COMMANDS = {**cli_settings.COMMANDS, **chatcli.COMMANDS, **voicecli.COMMANDS}


def _read_device_id(config: DeviceConfig) -> Optional[str]:
    f = Path(config.state_dir) / "device_id"
    return f.read_text().strip() if f.exists() else None


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
            print(f"  Check:  pai-device status")
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
    op_id = data.get("operation_id")
    target = pid + (f"@{version}" if version else "")
    if args.json and (args.no_wait or not op_id):
        _print_json(data)
        return 0
    if not op_id:                       # an older backend without operations: the legacy poll
        print(f"Install requested: {target}", file=sys.stderr)
        return await _watch_install(client, device_id, pid, version=version, target_shas=target_shas)
    if args.no_wait:
        print(f"Install requested: {target} on {device_id}\noperation {op_id}")
        dim(f"Follow it: pai-device operations --active")
        return 0
    print(f"Installing {target} on {device_id} …")
    code = await follow_operation(client, op_id, label=f"installing {target}", timeout=args.timeout)
    if args.json:
        _print_json(await client.get_operation(op_id))
    return code

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
        print(f"Available catalog:  pai-device list")
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

    # Parameters: --param k=v (repeatable), --json-params '<json>' | @file.json (alias --params/-p);
    # with none of them, JSON piped on stdin is used.
    json_text = args.json_params
    if json_text is None and not args.param and not sys.stdin.isatty():
        json_text = sys.stdin.read().strip() or None
    try:
        params = parse_params(args.param, json_text)
    except (ValueError, OSError) as exc:
        print(f"error: bad parameters: {exc}", file=sys.stderr)
        return 2

    resp = await client.invoke_capability(
        device_id=device_id,
        capability=args.capability,
        parameters=params,
        timeout=args.timeout,
    )

    if resp.get("awaiting_confirmation"):
        task_id = resp.get("task_id")
        if args.json and not args.approve:
            _print_json(resp)
            return 3
        print_confirmation(resp.get("confirmation") or {"capability": args.capability},
                           task_id=None if args.approve else task_id, out=print)
        if not args.approve:
            dim("Not approved: nothing ran. Re-run with --approve, or approve it in the PAI app.")
            return 3
        await client.confirm_task(task_id, scope=args.scope)
        dim(f"approved ({args.scope}); waiting for the result…")
        task = await wait_for_task(client, task_id, timeout=args.timeout)
        return print_task_outcome(task, as_json=args.json)

    if args.json:
        _print_json(resp)
        return 0 if resp.get("ok") else 1

    if resp.get("ok"):
        result = resp.get("result")
        # Plugins answer {"ok": ..., "data": ...}; show the payload, not the envelope.
        if isinstance(result, dict) and "data" in result and result.get("ok", True) is not False:
            result = result["data"]
        _print_json(result if result is not None else {"status": resp.get("status")})
        return 0

    result = resp.get("result")
    error = (resp.get("error") or (result.get("error") if isinstance(result, dict) else None)
             or resp.get("status"))
    print(f"✗ {error}", file=sys.stderr)
    if isinstance(result, dict) and result.get("data") is not None:
        _print_json(result["data"])
    return 1


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
            print(f"  Check:  pai-device status")
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
    op_id = data.get("operation_id")
    if args.json and (args.no_wait or not op_id):
        _print_json(data)
        return 0
    if not op_id:                       # an older backend without operations
        return await _watch_uninstall(client, device_id, args.plugin_id)
    if args.no_wait:
        print(f"Uninstall requested: {args.plugin_id} on {device_id}\noperation {op_id}")
        return 0
    print(f"Removing {args.plugin_id} from {device_id} …")
    code = await follow_operation(client, op_id, label=f"removing {args.plugin_id}", timeout=args.timeout)
    if args.json:
        _print_json(await client.get_operation(op_id))
    return code

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def _dispatch(
    args, client: APIClient, device_id: Optional[str], config: Optional[DeviceConfig] = None,
) -> int:
    cmd = args.command
    handler = MODULE_COMMANDS.get(cmd)
    if handler is not None:
        return await handler(args, Ctx(client=client, device_id=device_id, config=config))
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


def _print_http_error(exc: httpx.HTTPStatusError) -> None:
    """The server's own words: ``✗ message`` plus a dim ``(HTTP 403 code)`` line."""
    message, code = error_text(exc)
    print(f"✗ {message}", file=sys.stderr)
    dim(f"  (HTTP {exc.response.status_code}{' ' + code if code else ''} on {exc.request.method} {exc.request.url.path})")


async def _amain(args) -> int:
    try:
        config = DeviceConfig.from_env()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    client = APIClient(config.api_base, config.email, config.password)
    try:
        await client.login()
        client.device_id = _read_device_id(config)      # THIS device: acting on another one is remote
        device_id = args.device or client.device_id
        return await _dispatch(args, client, device_id, config)
    except httpx.HTTPStatusError as exc:
        _print_http_error(exc)
        return 1
    except httpx.TransportError as exc:
        print(f"✗ can't reach the backend at {config.api_base}: {exc}", file=sys.stderr)
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
        prog="pai-device",
        description="PAI on this device: talk to the assistant (chat, voice), manage your plugins and "
                    "settings, run capabilities, or start the device agent (`pai-device run`).",
        epilog="Config comes from the environment: PAI_BACKEND_URL, PAI_SERVER_EMAIL, PAI_SERVER_PASSWORD "
               "(see `pai-device config show`).",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    sub.add_parser("run", help="start the device agent (same as `pai-server`; "
                               "accepts --reload --log-dir --verbose-http)")

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
        help="install a plugin on a device and follow it live (stages + clear errors)",
        description="Installing on ANOTHER device needs that device's \"Allow remote plugin install\" switch "
                    "(pai-device remote-access). Without --no-wait the command follows the install until it "
                    "finishes and exits non-zero if it fails.",
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
    sp.add_argument("--no-wait", action="store_true", help="only request it; print the operation id")
    sp.add_argument("--timeout", type=float, default=600.0, help="stop following after this many seconds")
    sp.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)      # old flag: following is the default

    sp = sub.add_parser(
        "uninstall", parents=[common],
        help="remove a plugin from a device and follow it live",
    )
    sp.add_argument("plugin_id")
    sp.add_argument("--no-wait", action="store_true", help="only request it; print the operation id")
    sp.add_argument("--timeout", type=float, default=120.0)
    sp.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)

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
        help="run a capability on a device and wait for the result",
        description="Goes through the real pipeline (authorization, confirmation, audit). Running on ANOTHER "
                    "device needs its \"Allow remote plugin use\" switch. An action that needs approval is "
                    "shown to you and does NOT run unless you pass --approve (or approve it in the app).",
    )
    sp.add_argument(
        "capability",
        help="capability id, e.g. browser.navigate or camera.capture",
    )
    sp.add_argument("--param", "-P", action="append", metavar="KEY=VALUE",
                    help="a parameter (repeatable); values like 3, true, [1,2] are typed, the rest are text")
    sp.add_argument(
        "--json-params", "--params", "-p", dest="json_params", default=None, metavar="JSON",
        help="parameters as a JSON object; '@file.json' reads a file; with no parameters at all, JSON on stdin is used",
    )
    sp.add_argument(
        "--timeout", type=float, default=60.0,
        help="seconds to wait for the plugin result (default: 60)",
    )
    sp.add_argument("--approve", action="store_true",
                    help="if the action needs confirmation, approve it (you have just seen what it will do)")
    sp.add_argument("--scope", choices=["once", "session", "always"], default="once",
                    help="how long an --approve lasts (the server limits what is allowed)")

    for module in (cli_settings, chatcli, voicecli):
        module.register(sub, common)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()                 # build once
    argcomplete.autocomplete(parser)         # arm completion on the same object
    args = parser.parse_args(argv)           # then parse
    if args.command == "run":
        return _run_agent([])
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


AGENT_FLAGS = ("--reload", "--log-dir", "--verbose-http")


def _run_agent(rest: List[str]) -> int:
    from ._entry import main as agent_main
    sys.argv = ["pai-device", *rest]
    return agent_main()


def entry(argv: Optional[List[str]] = None) -> int:
    """The ``pai-device`` command: the CLI, or the agent when started the old way (no arguments,
    ``run``, or the agent's own flags), so existing service files keep working."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "run" or args[0].startswith(AGENT_FLAGS):
        return _run_agent(args[1:] if args and args[0] == "run" else args)
    return main(args)


if __name__ == "__main__":
    raise SystemExit(entry())
