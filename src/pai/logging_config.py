"""Loguru configuration for the PAI backend.

Sinks installed by `configure_logging`:

  1. stderr               — application logs only. Silences high-frequency
                            background activity (heartbeats, authz decisions,
                            device WS chatter) at DEBUG/INFO, but lets
                            WARNING+ through so a genuine device or auth
                            failure always surfaces on the terminal.

  2. logs/app.log         — complete history. Everything except HTTP access
                            lines, at any level. This is the "what happened
                            around the incident" file.

  3. logs/debug.log       — background activity only. Heartbeats, authz
                            decisions, device WS messages. The high-frequency
                            noise that would otherwise drown out the
                            terminal. `tail -f` this when diagnosing device
                            connectivity or permission denials.

  4. logs/http_access.log — one line per HTTP request (or `.jsonl` if
     (or .jsonl)             `json_access=True`). Written by
                            `pai.api.middleware.logging.LoggingMiddleware`,
                            which marks its records with `http_access=True`.

Stdlib loggers (`uvicorn`, `httpx`, `sqlalchemy`, etc.) are routed through
`_InterceptHandler` so everything ends up in loguru — no mixed formats, no
lines lost to stdout, and the location attribution points at the real
caller instead of `logging:callHandlers`.
"""
from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger


# ===========================================================================
# stdlib -> loguru bridge
# ===========================================================================

class _InterceptHandler(logging.Handler):
    """Re-emit stdlib log records through loguru with correct depth.

    Depth walking matters: without it, every stdlib record is attributed
    to ``logging:callHandlers`` in the output, because that's the frame
    loguru lands on when ``depth=2`` is passed from ``emit``. The correct
    depth is "however many frames up from this method until we exit
    ``logging/__init__.py``", which varies depending on the call path
    (``logger.info`` vs ``logger.error(exc_info=True)`` vs ``Logger._log``
    invoked by a handler).

    The ``depth == 0`` clause ensures the loop advances past ``emit``
    itself — ``emit`` is in *this* file, not in ``logging.__file__``, so
    without the clause the loop would terminate immediately and depth
    would stay at 0, which is even worse (loguru would print internal
    details of loguru itself).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            # Custom level defined via logging.addLevelName() that
            # loguru doesn't know about — use the numeric value, loguru
            # accepts both.
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame and (
            depth == 0 or frame.f_code.co_filename == logging.__file__
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# ===========================================================================
# Noise classification
# ===========================================================================
#
# "Noise" is high-frequency background activity. Useful for post-mortem
# analysis, but too chatty for the terminal — a 10-device deployment
# produces roughly one terminal line every two seconds from heartbeats
# alone, drowning out the application logs you actually want to watch.
#
# Two considerations shape the classification:
#
#   1. Match on logger name AND on message prefix. The name is the
#      primary signal, but the message prefix is a safety net for
#      stdlib-emitted records whose `name` field might be stale (e.g.
#      if the intercept handler's depth calculation regresses).
#
#   2. Noise is level-aware. A `pai.devices.websocket` ERROR — "ownership
#      rejected (4003)" — must surface on the terminal; it's the exact
#      thing an operator needs to see without hunting through files.
#      Only DEBUG and INFO noise is silenced. WARNING and above always
#      reaches the terminal.

_AUTHZ_LOGGERS = (
    "pai.api.middleware.auth",
    "pai.security",
)

_DEVICE_LOGGERS = (
    "pai.api.routes.devices",
    "pai.devices",           # covers .manager, .registry, .websocket, .connection
)

_AUTHZ_MSG_PREFIXES = (
    "authz:",
    "permission denied",
)

_DEVICE_MSG_PREFIXES = (
    "Device message [",
    "WebSocket disconnected:",
    "WebSocket connected:",
    "Connection attached to device",
    "Connection detached from device",
)

# Levels below this are silenced on the terminal when they match the
# noise classification. WARNING (30) and above always pass through.
_TERMINAL_NOISE_CUTOFF = logging.INFO


def _is_authz(record: "dict[str, Any]") -> bool:
    """True if the record comes from the auth middleware or security layer."""
    name = record.get("name") or ""
    msg = (record.get("message") or "").lower()
    return (
        any(name.startswith(p) for p in _AUTHZ_LOGGERS)
        or any(msg.startswith(p) for p in _AUTHZ_MSG_PREFIXES)
    )


def _is_device(record: "dict[str, Any]") -> bool:
    """True if the record comes from device lifecycle (WS, registry, manager)."""
    name = record.get("name") or ""
    msg = record.get("message") or ""
    return (
        any(name.startswith(p) for p in _DEVICE_LOGGERS)
        or any(msg.startswith(p) for p in _DEVICE_MSG_PREFIXES)
    )


def _is_noise(record: "dict[str, Any]") -> bool:
    """True if the record is high-frequency background activity."""
    return _is_authz(record) or _is_device(record)


def _is_http(record: "dict[str, Any]") -> bool:
    """True if the record was emitted by the HTTP access middleware."""
    return bool(record["extra"].get("http_access"))


def _not_http(record: "dict[str, Any]") -> bool:
    """Inverse of `_is_http` — for sinks that take application logs only."""
    return not record["extra"].get("http_access")


def _should_log_to_terminal(record: "dict[str, Any]") -> bool:
    """True if this record should appear on stderr.

    Loguru treats the return value of `filter` as "log this record"
    (True) vs "drop it" (False). A function named `_should_silence_*`
    returning True would therefore do the *opposite* of its name —
    loguru keeps the record. This function is named for what loguru
    expects to avoid that trap.

    Rules:
      • http access records → never on terminal (dedicated sink only)
      • non-noise records → always keep
      • noise at DEBUG/INFO → drop (that's the point)
      • noise at WARNING+ → keep (WS ownership rejected, auth misconfig,
                              device unreachable mid-request — the
                              exact things an operator needs to see)
    """
    if _is_http(record):
        return False
    if not _is_noise(record):
        return True
    level_no = record["level"].no if record.get("level") else 0
    return level_no > _TERMINAL_NOISE_CUTOFF


# ===========================================================================
# Formats
# ===========================================================================

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


# ===========================================================================
# Stdlib logger taming
# ===========================================================================
#
# Loggers that are chatty at INFO and provide no value once routed
# through loguru. Setting them to WARNING (or higher) means real errors
# still appear, but routine per-request/per-statement lines are dropped
# before they reach the intercept handler.
#
# `uvicorn.access` is redundant with `LoggingMiddleware`, which produces
# a cleaner line with duration and status. `uvicorn.error` is *not*
# tamed — its startup/shutdown messages are useful.
#
# `httpx`/`httpcore` are tamed to WARNING here; if a developer wants
# outbound HTTP client logs for debugging, they can bump these loggers
# or set `--verbose-http` in the CLI (which is the agent-side equivalent).
#
_TAME_TO_WARNING = (
    "uvicorn.access",
    "httpx",
    "httpcore",
    "httpcore2",          # httpx2 uses httpcore2
    "chromadb",
    "sentence_transformers",
    "huggingface_hub",
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.engine.Engine", # SQL echo already gated by Database(echo=...)
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
    "aiosqlite"
)




def _tame_stdlib_loggers() -> None:
    """Downgrade noisy stdlib loggers to WARNING.

    Called from `configure_logging` after the intercept handler is
    installed, so the levels take effect before any real request runs.
    """
    for name in _TAME_TO_WARNING:
        logging.getLogger(name).setLevel(logging.WARNING)


# ===========================================================================
# Public entry point
# ===========================================================================

def configure_logging(
    log_dir: Path | str = "logs",
    level: str = "INFO",
    json_access: bool = False,
    debug_log_level: str = "DEBUG",
) -> None:
    """Install loguru sinks. Call once, before the app starts.

    Parameters
    ----------
    log_dir
        Directory for the log files. Created if absent. Relative paths
        resolve from the process CWD; pass an absolute path if the
        process may change directories.
    level
        Minimum level for the terminal (stderr) and `app.log` sinks.
        Applies to non-noise loggers — noise has its own level gate.
        `"INFO"` in prod, `"DEBUG"` in dev.
    json_access
        If ``True``, emit `http_access.jsonl` (one JSON object per line,
        loguru's `serialize=True`) instead of the human-readable
        `http_access.log`. Prefer JSON when the logs feed a structured
        aggregator (Loki, ELK, Datadog); prefer text for `tail -f` and
        `grep`.
    debug_log_level
        Minimum level for `debug.log` — the background-activity file.
        Defaults to DEBUG so heartbeats and per-message device traffic
        are captured. Set to `"INFO"` to reduce disk usage on long-lived
        deployments where you only care about connect/disconnect events.

    Raises
    ------
    OSError
        If `log_dir` cannot be created or written to. Better to fail
        loudly at startup than to silently swallow every log line.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Wipe any pre-existing sinks. Important when this runs more than
    # once in the same process — tests, reload loops, notebooks. The
    # default stderr sink from loguru's import-time setup is removed
    # by this too, which is why we re-add it explicitly below.
    logger.remove()

    # ── 1. Terminal — application only, silence low-severity noise ──
    # Filter: `_should_log_to_terminal` drops HTTP-access records and
    # low-severity noise, keeps everything else. WARNING+ from the
    # "noisy" loggers still surfaces because the filter is level-aware.
    logger.add(
        sys.stderr,
        level=level,
        format=_CONSOLE_FORMAT,
        filter=_should_log_to_terminal,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # ── 2. Full application log ──
    # Includes noise at all levels. This is the authoritative history —
    # when an incident happens, `grep` here to reconstruct everything.
    # Filter: `_not_http` only — noise is *included* here. Do NOT use
    # `_should_log_to_terminal`; that would silently drop heartbeat and
    # authz history from the very file meant to preserve it.
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

    # ── 3. Background activity log ──
    # Same content that's silenced on terminal, at the full debug level.
    # `tail -f logs/debug.log` when diagnosing "why did my device stop
    # reconnecting" or "why is the admin panel returning 403" without
    # having to filter through application-log noise.
    logger.add(
        log_dir / "debug.log",
        level=debug_log_level,
        format=_FILE_FORMAT,
        filter=lambda r: _is_noise(r) and not _is_http(r),
        rotation="10 MB",
        retention=5,
        compression="gz",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    # ── 4. HTTP access log ──
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

    # ── 5. Route stdlib loggers through loguru ──
    # `force=True` clears any handlers installed by an earlier
    # `basicConfig()` call (uvicorn installs its own; this overrides).
    logging.basicConfig(
        handlers=[_InterceptHandler()],
        level=0,               # capture everything; per-logger levels filter
        force=True,
    )

    # ── 6. Tame high-frequency stdlib loggers ──
    _tame_stdlib_loggers()

    # ── 7. Announcement ──
    # Logged at INFO so it's visible on the terminal and in app.log —
    # useful sanity check that `configure_logging` actually ran.
    logger.info(
        "Logging configured: level={}, debug_level={}, dir={}, "
        "http_access={}",
        level,
        debug_log_level,
        log_dir.resolve(),
        "jsonl" if json_access else "log",
    )