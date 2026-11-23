
from __future__ import annotations

import inspect
import logging
import sys
from typing import Any, Callable

from loguru import logger

from pai.config.models.logging import (
    LoggingConfig,
    LogFileConfig,
)


# ============================================================
# Standard logging -> Loguru
# ============================================================

class InterceptHandler(logging.Handler):
    """Forward standard-library logging records to Loguru."""

    def emit(self, record: logging.LogAny) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame = inspect.currentframe()
        depth = 0

        try:
            # Skip this handler's emit() frame and logging internals.
            while frame:
                filename = frame.f_code.co_filename

                if depth > 0 and filename != logging.__file__:
                    break

                frame = frame.f_back
                depth += 1

            logger.bind(
                stdlib_logger=record.name,
            ).opt(
                depth=depth,
                exception=record.exc_info,
            ).log(
                level,
                record.getMessage(),
            )

        finally:
            del frame

# ============================================================
# Noise classification
# ============================================================

def _matches_logger(name: str, prefixes: list[str]) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in prefixes
    )


def _matches_message(message: str, prefixes: list[str]) -> bool:
    return any(
        message.startswith(prefix)
        for prefix in prefixes
    )


def _is_authz(
    record: Any,
    config: LoggingConfig,
) -> bool:
    name = record.get("name") or ""
    message = (record.get("message") or "").lower()

    return (
        _matches_logger(name, config.noise.authz_loggers)
        or _matches_message(
            message,
            config.noise.authz_message_prefixes,
        )
    )


def _is_device(
    record: Any,
    config: LoggingConfig,
) -> bool:
    name = record.get("name") or ""
    message = record.get("message") or ""

    return (
        _matches_logger(name, config.noise.device_loggers)
        or _matches_message(
            message,
            config.noise.device_message_prefixes,
        )
    )


def _is_noise(
    record: Any,
    config: LoggingConfig,
) -> bool:
    return (
        _is_authz(record, config)
        or _is_device(record, config)
    )


def _is_http(record: Any) -> bool:
    return bool(record["extra"].get("http_access"))


def _not_http(record: Any) -> bool:
    return not _is_http(record)


# ============================================================
# Filters
# ============================================================

def _terminal_filter(
    config: LoggingConfig,
) -> Callable[[Any], bool]:

    cutoff = logger.level(
        config.noise.terminal_cutoff
    ).no

    def filter_record(record: Any) -> bool:
        if _is_http(record):
            return False

        if not _is_noise(record, config):
            return True

        return record["level"].no > cutoff

    return filter_record


def _debug_filter(
    config: LoggingConfig,
) -> Callable[[Any], bool]:

    def filter_record(record: Any) -> bool:
        return (
            _is_noise(record, config)
            and not _is_http(record)
        )

    return filter_record


# ============================================================
# Formats
# ============================================================

CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
    "<level>{level: <8}</level> "
    "<cyan>{name}</cyan>:"
    "<cyan>{function}</cyan>:"
    "<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} "
    "{level: <8} "
    "{name}:{function}:{line} - {message}"
)

ACCESS_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} "
    "{extra[client]: <15} "
    "{extra[method]: <6} "
    "{extra[status]} "
    "{extra[duration_s]:>7.3f}s  "
    "{extra[path]}"
)


# ============================================================
# Sink registration
# ============================================================

def _add_file_sink(
    config: LoggingConfig,
    sink: LogFileConfig,
    *,
    filter: Callable | None = None,
    format: str = FILE_FORMAT,
    serialize: bool = False,
) -> None:

    if not sink.enabled:
        return

    logger.add(
        config.log_dir / sink.filename,
        level=sink.level,
        format=format,
        filter=filter,
        rotation=sink.rotation,
        retention=sink.retention,
        compression=sink.compression,
        serialize=serialize,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )


# ============================================================
# Third-party logger configuration
# ============================================================

def _configure_stdlib_logging(
    config: LoggingConfig,
) -> None:

    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=logging.NOTSET,
        force=True,
    )

    for name in config.third_party.warning:
        logging.getLogger(name).setLevel(logging.WARNING)

    for name in config.third_party.critical:
        logging.getLogger(name).setLevel(logging.CRITICAL)


# ============================================================
# Public API
# ============================================================

def configure_logging(config: LoggingConfig) -> None:
    """Configure Loguru using validated Pydantic settings."""

    config.log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Remove existing Loguru sinks.
    logger.remove()

    # --------------------------------------------------------
    # 1. Terminal
    # --------------------------------------------------------

    if config.terminal.enabled:
        logger.add(
            sys.stderr,
            level=config.terminal.level,
            format=CONSOLE_FORMAT,
            filter=_terminal_filter(config),
            colorize=config.terminal.colorize,
            backtrace=False,
            diagnose=False,
        )

    # --------------------------------------------------------
    # 2. Complete application history
    # --------------------------------------------------------

    _add_file_sink(
        config,
        config.app_file,
        filter=_not_http,
    )

    # --------------------------------------------------------
    # 3. Background activity
    # --------------------------------------------------------

    _add_file_sink(
        config,
        config.debug_file,
        filter=_debug_filter(config),
    )

    # --------------------------------------------------------
    # 4. HTTP access logs
    # --------------------------------------------------------

    if config.json_access:
        access_filename = (
            config.access_file.filename.rsplit(".", 1)[0]
            + ".jsonl"
        )

        access_config = config.access_file.model_copy(
            update={"filename": access_filename}
        )

        _add_file_sink(
            config,
            access_config,
            filter=_is_http,
            serialize=True,
        )

    else:
        _add_file_sink(
            config,
            config.access_file,
            filter=_is_http,
            format=ACCESS_FORMAT,
        )

    # --------------------------------------------------------
    # 5. Standard-library logger interception
    # --------------------------------------------------------

    _configure_stdlib_logging(config)

    logger.info(
        "Logging configured: level={}, directory={}, access_format={}",
        config.terminal.level,
        config.log_dir.resolve(),
        "JSONL" if config.json_access else "text",
    )
