#!/usr/bin/env python3
"""Drive one plugin .so for SEVERAL calls in a single process.

`pai_test.py` is one call per process; plugins that keep state between calls (e.g. the input
plugin's approved remote-desktop session) need many calls in one. Reads one JSON object per
stdin line, {"capability": "...", "params": {...}}, loads the library once, writes one JSON line
per call: {"error": ..., "result": {...}}. A line {"env": {"NAME": "value"|null}} changes the process
environment between calls. Exits at EOF.

    printf '%s\n' '{"capability":"input.status"}' | pai_session.py path/to/libpai_input.so
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pai_test import invoke, load_plugin  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    lib, module = load_plugin(Path(sys.argv[1]))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        call = json.loads(line)
        if "env" in call:                    # {"env": {"NAME": "value" | null}}: change the process env between calls
            for name, value in call["env"].items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = str(value)
            print(json.dumps({"error": None, "result": {"ok": "true"}}), flush=True)
            continue
        out = invoke(lib, module, call["capability"], call.get("params") or {})
        print(json.dumps({"error": out.get("error"), "result": out.get("result") or {}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
