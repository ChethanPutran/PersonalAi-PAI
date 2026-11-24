"""Serve the REAL app on a port with a fully isolated config (temp DB/logs/traces, fake voice).

    python tests/admin/serve_isolated.py --port 8765 --data-dir /tmp/pai-admin-demo

Used by the real-HTTP test and by the screenshot tool. Prints "READY <port>" once listening.
Nothing here touches ./data. The admin account is admin@example.com / password123.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PAI_ADMIN_EMAILS", "admin@example.com")

import uvicorn  # noqa: E402

from admin_helpers import isolated_config, seed_traces  # noqa: E402
from pai.server.factory import create_application  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--no-test-caps", action="store_true")
    ap.add_argument("--no-seed-traces", action="store_true", help="do not append demo traces to the trace store")
    a = ap.parse_args()
    tmp = Path(a.data_dir or tempfile.mkdtemp(prefix="pai-admin-"))
    tmp.mkdir(parents=True, exist_ok=True)
    app = create_application(isolated_config(tmp, test_capabilities=not a.no_test_caps))
    cfg = uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning")
    server = uvicorn.Server(cfg)

    original_startup = server.startup

    async def startup(sockets=None):
        await original_startup(sockets)
        if not a.no_seed_traces:
            from pai.observability.tracer import get_tracer
            seed_traces(get_tracer().store)
        print(f"READY {a.port}", flush=True)
    server.startup = startup
    server.run()


if __name__ == "__main__":
    main()
