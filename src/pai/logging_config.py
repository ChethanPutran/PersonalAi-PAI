"""Loguru configuration for the PAI backend.

Three sinks:

  1. stderr                — application logs (colorized, human-readable)
  2. logs/app.log          — same content, persisted (rotation + gzip)
  3. logs/http_access.log  — one line per HTTP request

HTTP access logs are distinguished from application logs by the
`extra["http_access"] = True` marker, which `LoggingMiddleware` binds.
Sinks 1 and 2 filter *out* that marker; sink 3 filters it *in*.

Stdlib loggers (uvicorn, httpx, sqlalchemy) are routed through an
InterceptHandler so everything ends up in loguru — no mixed formats,
no lines lost to stdout.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger


# ── stdlib → loguru bridge ──────────────────────────────────────────
class _InterceptHandler(logging.Handler):
    """Re-emit stdlib log records through loguru with correct depth."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the stack past logging internals so the logged
        # location points at the original caller, not this handler.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# ── formats ─────────────────────────────────────────────────────────
_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
    "- <level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} "
    "{name}:{function}:{line} - {message}"
)

_ACCESS_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} "
    "{extra[client]: <15} "
    "{extra[method]: <6} "
    "{extra[status]} "
    "{extra[duration_s]:>7.3f}s  "
    "{extra[path]}"
)


# ── public entry point ──────────────────────────────────────────────
def configure_logging(
    log_dir: Path | str = "logs",
    level: str = "INFO",
    json_access: bool = False,
) -> None:
    """
    Install loguru sinks. Call once, before the app starts.

    log_dir       — where logs live (default: ./logs, relative to CWD)
    level         — minimum level for stderr + app.log
    json_access   — if True, emit http_access.jsonl (serialize=True)
                    instead of the human-readable http_access.log
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Wipe any pre-existing sinks (default stderr, or handlers left
    # over from a previous call in tests).
    logger.remove()

    _is_http = lambda r: bool(r["extra"].get("http_access"))    # noqa: E731
    _not_http = lambda r: not r["extra"].get("http_access")     # noqa: E731

    # ── 1. Terminal (application only) ──
    logger.add(
        sys.stderr,
        level=level,
        format=_CONSOLE_FORMAT,
        filter=_not_http,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # ── 2. File: full application log ──
    logger.add(
        log_dir / "app.log",
        level=level,
        format=_FILE_FORMAT,
        filter=_not_http,
        rotation="20 MB",
        retention=10,
        compression="gz",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )

    # ── 3. File: HTTP access log ──
    if json_access:
        logger.add(
            log_dir / "http_access.jsonl",
            level="INFO",
            serialize=True,
            filter=_is_http,
            rotation="20 MB",
            retention=10,
            compression="gz",
            enqueue=True,
        )
    else:
        logger.add(
            log_dir / "http_access.log",
            level="INFO",
            format=_ACCESS_FORMAT,
            filter=_is_http,
            rotation="20 MB",
            retention=10,
            compression="gz",
            enqueue=True,
        )

    # ── 4. Route stdlib loggers through loguru ──
    logging.basicConfig(
        handlers=[_InterceptHandler()],
        level=0,                     # capture everything; loguru filters
        force=True,                  # override any prior basicConfig
    )

    # Tame noisy stdlib loggers.
    #   uvicorn.access — redundant with our LoggingMiddleware
    #   httpx/httpcore — outbound HTTP client chatter (backend calls)
    for name in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logger.debug(
        "Logging configured: level={}, dir={}, json_access={}",
        level, log_dir, json_access,
    )