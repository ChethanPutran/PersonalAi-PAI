"""Entry point for the PAI device agent.

Kept out of __main__.py so it can be imported by both:
  - server/__init__.py  (for the `server` console script)
  - server/__main__.py  (for `python -m server`)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
from pathlib import Path

from .agent.agent import DeviceAgent
from .config import DeviceConfig

logger = logging.getLogger(__name__)

_SRC_DIR = Path(__file__).resolve().parent
_QUIET_IN_TERMINAL = ("httpx", "httpcore", "urllib3")


def _configure_logging(log_dir: Path, verbose_http: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    http_log = log_dir / "http.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    if not verbose_http:
        console.addFilter(
            lambda r: not r.name.startswith(_QUIET_IN_TERMINAL)
        )
    root.addHandler(console)

    fh = logging.handlers.RotatingFileHandler(
        http_log, maxBytes=10 * 1024 * 1024, backupCount=5,
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"
    ))
    for name in _QUIET_IN_TERMINAL:
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        lg.addHandler(fh)


def _run_once(cfg: DeviceConfig) -> int:
    try:
        asyncio.run(DeviceAgent(cfg).run())
    except KeyboardInterrupt:
        pass
    return 0


def _spawn_child() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "server"],
        env=os.environ.copy(),
    )


def _stop_child(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("child did not exit in 10s — killing")
        proc.kill()
        proc.wait()


def _run_reload() -> int:
    try:
        from watchfiles import PythonFilter, watch
    except ImportError:
        print(
            "watchfiles not installed — run:\n"
            "  uv add --dev watchfiles",
            file=sys.stderr,
        )
        return 2

    proc = _spawn_child()
    logger.info(
        "[reload] agent started (pid %d), watching %s",
        proc.pid, _SRC_DIR,
    )

    try:
        for changes in watch(_SRC_DIR, watch_filter=PythonFilter()):
            changed = sorted({
                str(Path(p).relative_to(_SRC_DIR)) for _, p in changes
            })
            logger.info("[reload] %s — restarting", ", ".join(changed))
            _stop_child(proc)
            proc = _spawn_child()
            logger.info("[reload] agent restarted (pid %d)", proc.pid)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_child(proc)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="server")
    p.add_argument(
        "--reload", action="store_true",
        help="restart the agent when a .py file under src/server/ changes "
             "(dev only; requires `uv add --dev watchfiles`)",
    )
    p.add_argument(
        "--log-dir", default="logs/server",
        help="directory for http.log (default: ./logs/server)",
    )
    p.add_argument(
        "--verbose-http", action="store_true",
        help="also print httpx logs to the terminal (default: file only)",
    )
    args = p.parse_args()

    _configure_logging(Path(args.log_dir), verbose_http=args.verbose_http)

    try:
        cfg = DeviceConfig.from_env()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(
            "\nRequired environment variables:\n"
            "  PAI_BACKEND_URL   e.g. http://localhost:8000\n"
            "  PAI_SERVER_EMAIL  registered user email\n"
            "  PAI_SERVER_PASSWORD      that user's password\n"
            "Optional:\n"
            "  PAI_DEVICE_NAME   defaults to hostname\n"
            "  PAI_DEVICE_TYPE   defaults to 'server'\n",
            file=sys.stderr,
        )
        return 2

    if args.reload:
        return _run_reload()

    return _run_once(cfg)
