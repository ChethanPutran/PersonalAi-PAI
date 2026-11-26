"""CLI commands for settings and account: ``settings``, ``remote-access``, ``profile``,
``password``, ``devices``, ``plugins``, ``operations``, ``approve``, ``deny``, ``config``.

Each command is ``async def cmd_x(args, ctx) -> int``; ``register`` adds the parsers and
``COMMANDS`` maps the command name to its handler (see ``cli.py``).
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from .cli_support import (
    Ctx, dim, error_text, parse_on_off, print_json, print_table, print_task_outcome, wait_for_task,
    yes_no,
)

REMOTE_HELP = (
    "Both switches are OFF by default. While off, nothing outside this device (your other devices, "
    "the web, schedules, agents) can install plugins on it or run plugins on it. "
    "Acting on the device from itself is always allowed."
)


def _need_device(args, ctx: Ctx) -> Optional[str]:
    device_id = getattr(args, "device", None) or ctx.device_id
    if not device_id:
        print("error: no device id: run `pai-device run` once so this device registers, or pass --device",
              file=sys.stderr)
    return device_id


def _short(text: Optional[str], n: int = 8) -> str:
    return (text or "")[:n]


# ---------------------------------------------------------------------------
# Remote access
# ---------------------------------------------------------------------------

def _print_remote(state: Dict[str, Any]) -> None:
    print(f"Remote access for {state.get('name') or state.get('device_id')}:")
    print(f"  allow remote plugin install : {yes_no(state.get('allow_remote_install'))}")
    print(f"  allow remote plugin use     : {yes_no(state.get('allow_remote_invoke'))}")


async def cmd_remote_access(args, ctx: Ctx) -> int:
    device_id = _need_device(args, ctx)
    if not device_id:
        return 1
    if args.action == "show":
        state = await ctx.client.get_remote_access(device_id)
    else:
        install, invoke = parse_on_off(args.install), parse_on_off(args.invoke)
        if install is None and invoke is None:
            print("error: give --install on|off and/or --invoke on|off", file=sys.stderr)
            return 2
        state = await ctx.client.set_remote_access(device_id, install=install, invoke=invoke)
    if args.json:
        print_json(state)
        return 0
    _print_remote(state)
    if args.action == "show":
        dim(REMOTE_HELP)
    return 0


# ---------------------------------------------------------------------------
# Profile / password
# ---------------------------------------------------------------------------

def _print_profile(p: Dict[str, Any]) -> None:
    print(f"Profile ({p.get('email')}):")
    print(f"  display name : {p.get('display_name') or '(not set)'}")
    print(f"  time zone    : {p.get('timezone') or '(not set)'}")
    print(f"  language     : {p.get('locale') or '(not set)'}")


async def cmd_profile(args, ctx: Ctx) -> int:
    if args.action == "show":
        profile = await ctx.client.get_profile()
    else:
        fields = {k: v for k, v in (("display_name", args.name), ("timezone", args.timezone),
                                    ("locale", args.language)) if v is not None}
        if not fields:
            print("error: give --name, --timezone and/or --language (an empty value clears it)", file=sys.stderr)
            return 2
        profile = await ctx.client.update_profile(**fields)
    if args.json:
        print_json(profile)
    else:
        _print_profile(profile)
    return 0


async def cmd_password(args, ctx: Ctx) -> int:
    if not sys.stdin.isatty():
        print("error: `password` asks on the terminal (it never takes a password as an argument)", file=sys.stderr)
        return 2
    current = await asyncio.to_thread(getpass.getpass, "Current password: ")
    new = await asyncio.to_thread(getpass.getpass, "New password (8-72 characters): ")
    again = await asyncio.to_thread(getpass.getpass, "Repeat new password: ")
    if new != again:
        print("✗ the new passwords don't match", file=sys.stderr)
        return 1
    if not 8 <= len(new) <= 72:
        print("✗ the new password must be 8 to 72 characters", file=sys.stderr)
        return 1
    await ctx.client.change_password(current, new)
    print("✓ password updated")
    dim("Other apps stay signed in until their session ends. Update PAI_SERVER_PASSWORD for this agent.")
    return 0


# ---------------------------------------------------------------------------
# Devices, plugins, operations
# ---------------------------------------------------------------------------

async def cmd_devices(args, ctx: Ctx) -> int:
    data = await ctx.client.account_devices()
    if args.json:
        print_json(data)
        return 0
    rows = [(
        ("* " if d["id"] == ctx.device_id else "  ") + (d.get("name") or "?"),
        d.get("platform"), "online" if d.get("connected") else "offline",
        yes_no(d.get("allow_remote_install")), yes_no(d.get("allow_remote_invoke")),
        d.get("last_seen") or "never", d["id"],
    ) for d in data.get("devices", [])]
    print_table(rows, ["DEVICE", "PLATFORM", "STATUS", "REMOTE INSTALL", "REMOTE USE", "LAST SEEN", "ID"])
    dim("* = this device. Change the switches with: pai-device remote-access set --install on|off --invoke on|off")
    return 0


async def cmd_plugins(args, ctx: Ctx) -> int:
    pid = getattr(args, "plugin_id", None)
    if args.action in ("enable", "disable"):
        if not pid:
            print(f"error: pai-device plugins {args.action} <plugin_id>", file=sys.stderr)
            return 2
        data = await ctx.client.request_json("POST", f"/plugins/{pid}/{args.action}", json={})
        print_json(data) if args.json else print(f"✓ {pid} {args.action}d for your account")
        return 0

    data = await ctx.client.account_plugins()
    installed: Dict[str, Dict[str, Any]] = {}
    device_id = None if args.no_device else (args.device or ctx.device_id)
    if device_id:
        try:
            for rec in (await ctx.client.list_installed(device_id)).get("plugins", []):
                installed[rec["plugin_id"]] = rec
        except httpx.HTTPError:
            device_id = None                              # not ours / not reachable: just skip the columns
    if args.json:
        print_json({"plugins": data.get("plugins", []), "installed": installed})
        return 0
    rows = []
    for p in data.get("plugins", []):
        row = [p["id"], p["name"], p["version"], ",".join(p.get("platforms") or []),
               yes_no(p.get("enabled"))]
        if device_id:
            rec = installed.get(p["id"]) or {}
            row += ["yes" if rec.get("is_installed") else "no",
                    yes_no(rec.get("enabled_on_device")) if rec.get("is_installed") else "-"]
        rows.append(row)
    headers = ["ID", "NAME", "VERSION", "PLATFORMS", "ACCOUNT"] + (["INSTALLED", "ON DEVICE"] if device_id else [])
    print_table(rows, headers)
    dim("ACCOUNT = switched on for your account; a plugin must also be installed on a device to run there.")
    return 0


async def cmd_operations(args, ctx: Ctx) -> int:
    device_id = args.device
    data = await ctx.client.list_operations(
        device_id=device_id, plugin_id=args.plugin, active=args.active, limit=args.limit)
    if args.json:
        print_json(data)
        return 0
    rows = []
    for o in data.get("operations", []):
        err = (o.get("error") or {}).get("message") or ""
        rows.append((_short(o["id"]), o["plugin_id"] + (f"@{o['version']}" if o.get("version") else ""),
                     o["kind"], _short(o["device_id"]), o["status"], (o.get("updated_at") or "")[:19], err[:60]))
    print_table(rows, ["OP", "PLUGIN", "KIND", "DEVICE", "STATUS", "UPDATED", "ERROR"])
    return 0


# ---------------------------------------------------------------------------
# Approving a task that waits for confirmation
# ---------------------------------------------------------------------------

async def cmd_approve(args, ctx: Ctx) -> int:
    resp = await ctx.client.confirm_task(args.task_id, scope=args.scope)
    grant = resp.get("grant")
    print(f"✓ approved ({args.scope})" + (" and remembered" if grant else ""), file=sys.stderr)
    if args.no_wait:
        print_json(resp) if args.json else None
        return 0
    task = await wait_for_task(ctx.client, args.task_id, timeout=args.timeout)
    return print_task_outcome(task, as_json=args.json)


async def cmd_deny(args, ctx: Ctx) -> int:
    resp = await ctx.client.deny_task(args.task_id)
    print_json(resp) if args.json else print("✓ denied: the action will not run")
    return 0


# ---------------------------------------------------------------------------
# Local configuration and the combined ``settings`` view
# ---------------------------------------------------------------------------

def _local_config(ctx: Ctx) -> Dict[str, Any]:
    c = ctx.config
    return {
        "backend_url": c.backend_url, "api_base": c.api_base, "email": c.email, "password": "********",
        "device_name": c.device_name, "device_type": c.device_type, "platform": c.platform,
        "architecture": c.architecture, "device_id": ctx.device_id,
        "state_dir": str(c.state_dir), "plugin_dir": str(c.plugin_dir),
        "heartbeat_interval": c.heartbeat_interval, "reconcile_interval": c.reconcile_interval,
    }


async def cmd_config(args, ctx: Ctx) -> int:
    cfg = _local_config(ctx)
    if args.json:
        print_json(cfg)
        return 0
    width = max(len(k) for k in cfg)
    for k, v in cfg.items():
        print(f"{k:<{width}}  {v}")
    dim("Local settings come from the environment (PAI_BACKEND_URL, PAI_SERVER_EMAIL, PAI_SERVER_PASSWORD, "
        "PAI_DEVICE_NAME, PAI_DEVICE_TYPE, PAI_HOME); the password is never shown.")
    return 0


async def cmd_settings(args, ctx: Ctx) -> int:
    device_id = ctx.device_id if not args.device else args.device
    if args.action == "set":
        profile_fields = {k: v for k, v in (("display_name", args.name), ("timezone", args.timezone),
                                            ("locale", args.language)) if v is not None}
        install, invoke = parse_on_off(args.remote_install), parse_on_off(args.remote_invoke)
        if not profile_fields and install is None and invoke is None:
            print("error: nothing to set (see --help)", file=sys.stderr)
            return 2
        if profile_fields:
            await ctx.client.update_profile(**profile_fields)
        if install is not None or invoke is not None:
            if not device_id:
                print("error: no device id for the remote-access switches", file=sys.stderr)
                return 1
            await ctx.client.set_remote_access(device_id, install=install, invoke=invoke)
        print("✓ saved", file=sys.stderr)

    profile = await ctx.client.get_profile()
    remote = await ctx.client.get_remote_access(device_id) if device_id else None
    if args.json:
        print_json({"profile": profile, "remote_access": remote, "local": _local_config(ctx)})
        return 0
    _print_profile(profile)
    print()
    if remote:
        _print_remote(remote)
        dim(REMOTE_HELP)
    else:
        print("(this device is not registered yet: run `pai-device run` once)")
    print()
    print(f"Local: backend {ctx.config.api_base}, device '{ctx.config.device_name}' ({ctx.config.device_type}); "
          f"`pai-device config show` for all of it.")
    return 0


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def register(sub: "argparse._SubParsersAction", common: argparse.ArgumentParser) -> None:
    sp = sub.add_parser("settings", parents=[common], help="show or change your profile and this device's remote-access switches")
    sp.add_argument("action", choices=["show", "set"], nargs="?", default="show")
    sp.add_argument("--name", help="display name (empty clears)")
    sp.add_argument("--timezone", help="IANA time zone, e.g. Asia/Kolkata (empty clears)")
    sp.add_argument("--language", help="language, e.g. en or en-IN (empty clears)")
    sp.add_argument("--remote-install", metavar="on|off", help="allow other devices to install plugins here")
    sp.add_argument("--remote-invoke", metavar="on|off", help="allow other devices/schedules/agents to run plugins here")

    sp = sub.add_parser("remote-access", parents=[common], help="who may install / run plugins on this device from outside it",
                        description=REMOTE_HELP)
    sp.add_argument("action", choices=["show", "set"], nargs="?", default="show")
    sp.add_argument("--install", metavar="on|off", help="allow remote plugin install/uninstall/enable")
    sp.add_argument("--invoke", metavar="on|off", help="allow remote plugin use (run capabilities)")

    sp = sub.add_parser("profile", parents=[common], help="show or change your profile")
    sp.add_argument("action", choices=["show", "set"], nargs="?", default="show")
    sp.add_argument("--name", help="display name (empty clears)")
    sp.add_argument("--timezone", help="IANA time zone, e.g. Asia/Kolkata (empty clears)")
    sp.add_argument("--language", help="language, e.g. en or en-IN (empty clears)")

    sub.add_parser("password", parents=[common], help="change your password (prompts; never echoes)")
    sub.add_parser("devices", parents=[common], help="your devices and their remote-access switches")

    sp = sub.add_parser("plugins", parents=[common], help="your plugins: list, enable, disable (account level)")
    sp.add_argument("action", choices=["list", "enable", "disable"], nargs="?", default="list")
    sp.add_argument("plugin_id", nargs="?", help="plugin id for enable/disable")
    sp.add_argument("--no-device", action="store_true", help="don't add this device's install state to the list")

    sp = sub.add_parser("operations", parents=[common], help="recent plugin installs/removals and how they went")
    sp.add_argument("--plugin")
    sp.add_argument("--active", action="store_true", help="only operations still running")
    sp.add_argument("--limit", type=int, default=30)

    sp = sub.add_parser("approve", parents=[common], help="approve a task that is waiting for your confirmation")
    sp.add_argument("task_id")
    sp.add_argument("--scope", choices=["once", "session", "always"], default="once",
                    help="how long the approval lasts (the server limits what is allowed)")
    sp.add_argument("--no-wait", action="store_true", help="don't wait for the result")
    sp.add_argument("--timeout", type=float, default=120.0)

    sp = sub.add_parser("deny", parents=[common], help="deny a task that is waiting for your confirmation")
    sp.add_argument("task_id")

    cp = sub.add_parser("config", parents=[common], help="show the effective local configuration (password masked)")
    cp.add_argument("action", choices=["show"], nargs="?", default="show")


COMMANDS: Dict[str, Callable[[Any, Ctx], Awaitable[int]]] = {
    "settings": cmd_settings, "remote-access": cmd_remote_access, "profile": cmd_profile,
    "password": cmd_password, "devices": cmd_devices, "plugins": cmd_plugins,
    "operations": cmd_operations, "approve": cmd_approve, "deny": cmd_deny, "config": cmd_config,
}
