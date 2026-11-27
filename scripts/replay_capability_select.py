"""Replay recorded planner calls against the capability selector.

Reads data/traces/*/*.jsonl, takes each `decomposer.decompose` span (goal, capabilities offered, capabilities
the plan used) and asks: would the selector have kept everything the plan used?  Descriptions come from the
plugin manifests under plugin_registry/ plus the capability ids in the trace.

    .venv/bin/python scripts/replay_capability_select.py [--top-families 10]
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from pai.planning.capability_select import select_capabilities

ROOT = Path(__file__).resolve().parents[1]


def manifest_caps() -> dict:
    caps: dict = {}
    for f in sorted(glob.glob(str(ROOT / "plugin_registry/plugins/*/*/*/manifest.json"))):
        try:
            m = json.load(open(f))
        except Exception:
            continue
        for c in m.get("capabilities", []):
            caps[c["id"]] = {"description": c.get("description", ""), "parameters": c.get("parameters") or {}}
    return caps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-families", type=int, default=4)
    ap.add_argument("--show-misses", action="store_true")
    a = ap.parse_args()
    known = manifest_caps()
    total = covered = shrunk = 0
    sizes = []
    for f in sorted(glob.glob(str(ROOT / "data/traces/*/*.jsonl"))):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("name") != "decomposer.decompose" or r.get("kind") != "span.end":
                continue
            at = r["attributes"]
            offered = at.get("capabilities") or []
            used = [c for c in (at.get("capabilities_used") or []) if c != "chat.reply"]
            if not offered:
                continue
            caps = {c: known.get(c, {"description": c, "parameters": {}}) for c in offered}
            sel = select_capabilities(at.get("goal", ""), caps, top_families=a.top_families)
            total += 1
            ok = all(c in sel.ids for c in used) if sel.confident else True
            covered += ok
            shrunk += sel.confident
            sizes.append(len(sel.ids) / len(offered))
            if not ok and a.show_misses:
                print("MISS", repr(at["goal"][:70]), "used", used, "kept", len(sel.ids), "of", len(offered))
    print(f"{total} plans; selector confident on {shrunk}; plan capabilities all kept in {covered}/{total}")
    print(f"avg share of capabilities shown: {sum(sizes) / max(len(sizes), 1):.0%}")


if __name__ == "__main__":
    main()
