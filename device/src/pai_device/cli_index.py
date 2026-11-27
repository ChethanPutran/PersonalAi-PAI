"""``pai-device index status | now | clear``: the document index of THIS device (Files plugin, "Index documents")."""
from __future__ import annotations

import argparse
import sys
from typing import Any, Awaitable, Callable, Dict, Optional

from .cli_support import Ctx, print_json, yes_no
from .indexer import DocumentIndexer, IndexStopped, PDF_INSTALL_HINT, enabled, pdf_available
from .plugin_config_store import PluginConfigStore

PLUGIN = "files"


def _need_device(args, ctx: Ctx) -> Optional[str]:
    device_id = getattr(args, "device", None) or ctx.device_id
    if not device_id:
        print("error: no device id: run `pai-device run` once so this device registers, or pass --device",
              file=sys.stderr)
    return device_id


async def _settings(ctx: Ctx, device_id: str) -> Dict[str, Any]:
    """The files plugin's effective settings: the backend's answer, else what this device stored locally."""
    try:
        state = await ctx.client.get_plugin_config(device_id, PLUGIN)
        eff = (state or {}).get("effective")
        if isinstance(eff, dict):
            return eff
    except Exception:
        pass
    return PluginConfigStore(ctx.config.state_dir / "plugin_config").load_all().get(PLUGIN, {})


def _indexer(ctx: Ctx, device_id: str) -> DocumentIndexer:
    return DocumentIndexer(ctx.client, device_id, ctx.config.state_dir)


def _skipped_text(skipped: Dict[str, int]) -> str:
    return ", ".join(f"{n} {reason.replace('_', ' ')}" for reason, n in sorted(skipped.items())) or "none"


async def cmd_index(args, ctx: Ctx) -> int:
    device_id = _need_device(args, ctx)
    if not device_id:
        return 1
    ix = _indexer(ctx, device_id)
    action = getattr(args, "action", "status") or "status"
    try:
        if action == "clear":
            await ix.clear()
            print("✓ cleared this device's indexed documents (server and local state)")
            return 0
        values = await _settings(ctx, device_id)
        if action == "now":
            return await _now(args, ix, values)
        server = await ix.server_status()
    except IndexStopped as exc:
        print(f"✗ {exc.message}", file=sys.stderr)
        return 1
    local = ix.state.count()
    data = {"indexing_enabled": enabled(values), "server": server, "local_files": local,
            "pypdf_available": pdf_available(), "folders": values.get("allowedPaths") or [],
            "denied": values.get("deniedPaths") or [], "max_file_mb": values.get("indexMaxFileMb", 20)}
    if args.json:
        print_json(data)
        return 0
    print(f"Document index of device {device_id[:8]}:")
    print(f"  indexing enabled (Files plugin) : {yes_no(data['indexing_enabled'])}")
    print(f"  on the server                   : {server.get('files', 0)} files, {server.get('chunks', 0)} chunks")
    print(f"  tracked locally                 : {local} files")
    print(f"  PDF support (pypdf)             : {yes_no(data['pypdf_available'])}")
    if not data["pypdf_available"]:
        print(f"    {PDF_INSTALL_HINT}")
    if not data["indexing_enabled"]:
        print("  Turn on 'Index documents' in the Files plugin settings, or: "
              "pai-device plugins config files --set indexDocuments=on")
    return 0


async def _now(args, ix: DocumentIndexer, values: Dict[str, Any]) -> int:
    if not enabled(values):
        report = await ix.apply(values, force_clear=False)
        print("Indexing is off for the Files plugin on this device: nothing was sent."
              + (" Removed what had been indexed." if report.message else ""))
        print("Turn it on: pai-device plugins config files --set indexDocuments=on")
        return 1
    last = {"n": -1}

    def progress(r) -> None:
        if not args.json and r.files_seen != last["n"] and r.files_seen % 25 == 0:
            last["n"] = r.files_seen
            print(f"  ... {r.files_seen} files looked at, {r.files_sent} sent", flush=True)

    report = await ix.run_once(values, progress=progress)
    if args.json:
        print_json(report.as_dict())
        return 1 if report.stopped else 0
    print(f"Files looked at : {report.files_seen}")
    print(f"Sent            : {report.files_sent} files, {report.chunks_sent} chunks")
    print(f"Unchanged       : {report.files_unchanged}")
    print(f"Removed         : {report.files_removed}")
    print(f"Skipped         : {_skipped_text(report.skipped)}")
    if report.truncated:
        print(f"Long files cut  : {report.truncated} (only the first part of very long files is indexed)")
    if report.hint:
        print(report.hint)
    if report.stopped:
        print(f"✗ stopped: {report.message}", file=sys.stderr)
        return 1
    print("✓ done")
    return 0


def register(sub: "argparse._SubParsersAction", common: argparse.ArgumentParser) -> None:
    sp = sub.add_parser("index", parents=[common], help="document search index of this device: status, now, clear")
    sp.add_argument("action", choices=["status", "now", "clear"], nargs="?", default="status")


COMMANDS: Dict[str, Callable[[Any, Ctx], Awaitable[int]]] = {"index": cmd_index}
