
from typing import Any

from loguru import logger

from pai.config.models.logging import LoggingConfig

def _is_noise(record: Any, config: LoggingConfig) -> bool:
    return (
        _is_authz(record, config)
        or _is_device(record, config)
    )


def _is_http(record: Any) -> bool:
    return bool(record["extra"].get("http_access"))

def _is_authz(record: Any, config: LoggingConfig) -> bool:
    name = (
        record["extra"].get("stdlib_logger")
        or record.get("name")
        or ""
    )
    message = (record.get("message") or "").lower()

    return (
        _matches_logger(name, config.noise.authz_loggers)
        or _matches_prefix(
            message,
            config.noise.authz_message_prefixes,
        )
    )


def _is_device(record: Any, config: LoggingConfig) -> bool:
    name = (
        record["extra"].get("stdlib_logger")
        or record.get("name")
        or ""
    )
    message = record.get("message") or ""

    return (
        _matches_logger(name, config.noise.device_loggers)
        or _matches_prefix(
            message,
            config.noise.device_message_prefixes,
        )
    )


def _not_http(record: Any) -> bool:
    return not _is_http(record)


def _terminal_filter(config: LoggingConfig):
    cutoff = logger.level(config.noise.terminal_cutoff).no

    def filter_record(record: Any) -> bool:
        if _is_http(record):
            return False

        if not _is_noise(record, config):
            return True

        return record["level"].no > cutoff

    return filter_record


def _debug_filter(config: LoggingConfig):
    def filter_record(record: Any) -> bool:
        return (
            _is_noise(record, config)
            and not _is_http(record)
        )

    return filter_record

# ---------------------------------------------------------
# Noise classification
# ---------------------------------------------------------

def _matches_prefix(value: str, prefixes: list[str]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def _matches_logger(name: str, names: list[str]) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in names
    )


