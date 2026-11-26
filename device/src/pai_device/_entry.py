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
from .config import DeviceConfig, StorageNotWritable, pai_home
from .config_file import ENV_VAR

logger = logging.getLogger(__name__)

_SRC_DIR = Path(__file__).resolve().parent
_QUIET_IN_TERMINAL = ("httpx", "httpcore", "urllib3")


def _configure_logging(log_dir: Path, verbose_http: bool = False) -> None:
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

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "http.log", maxBytes=10 * 1024 * 1024, backupCount=5,
        )
    except OSError as exc:       # an unwritable log dir must not stop the agent: log to the console only
        logging.getLogger(__name__).warning(
            "File logging disabled: cannot write %s (%s)", log_dir, exc.strerror or exc)
        return
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
        [sys.executable, "-m", "pai_device"],
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
        "--log-dir", default=None,
        help="directory for http.log (default: $PAI_HOME/logs, i.e. ~/.pai/logs)",
    )
    p.add_argument(
        "--config", default=None, metavar="FILE",
        help="YAML config file (default: $PAI_DEVICE_CONFIG, ./pai-device.yaml, ~/.pai/pai-device.yaml)",
    )
    p.add_argument(
        "--verbose-http", action="store_true",
        help="also print httpx logs to the terminal (default: file only)",
    )
    args = p.parse_args()

    if args.config:
        os.environ[ENV_VAR] = args.config        # the --reload child process reads it from here too

    try:
        cfg = DeviceConfig.load(args.config)
    except StorageNotWritable as exc:
        print(f"storage error: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(
            "\nSet these in a YAML file (see pai-device.yaml) or as environment variables:\n"
            "  backend.url       / PAI_BACKEND_URL       e.g. http://localhost:8000\n"
            "  account.email     / PAI_SERVER_EMAIL      registered user email\n"
            "  account.password  / PAI_SERVER_PASSWORD   that user's password (prefer the env var)\n"
            "Optional:\n"
            "  device.name       / PAI_DEVICE_NAME       defaults to hostname\n"
            "  device.type       / PAI_DEVICE_TYPE       defaults to 'server'\n"
            "Config file: --config FILE, $PAI_DEVICE_CONFIG, ./pai-device.yaml or ~/.pai/pai-device.yaml\n",
            file=sys.stderr,
        )
        return 2

    log_dir = Path(args.log_dir) if args.log_dir else (cfg.log_dir or pai_home() / "logs")
    _configure_logging(log_dir, verbose_http=args.verbose_http)
    if cfg.config_file:
        logger.info("Config file: %s", cfg.config_file)

    if args.reload:
        return _run_reload()

    try:
        return _run_once(cfg)
    except StorageNotWritable as exc:
        print(f"storage error: {exc}", file=sys.stderr)
        return 3
