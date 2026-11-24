
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


LogLevel = Literal[
    "TRACE", "DEBUG", "INFO", "SUCCESS",
    "WARNING", "ERROR", "CRITICAL"
]

class LogFileConfig(BaseModel):
    enabled: bool = True
    filename: str
    level: LogLevel = "INFO"
    rotation: str = "20 MB"
    retention: str = "10 days"
    compression: str | None = "gz"


class TerminalLogConfig(BaseModel):
    enabled: bool = True
    level: LogLevel = "INFO"
    colorize: bool = True


class NoiseFilterConfig(BaseModel):
    terminal_cutoff: LogLevel = "INFO"

    authz_loggers: list[str] = Field(default_factory=list)
    device_loggers: list[str] = Field(default_factory=list)

    authz_message_prefixes: list[str] = Field(default_factory=list)
    device_message_prefixes: list[str] = Field(default_factory=list)


class ThirdPartyLogConfig(BaseModel):
    warning: list[str] = Field(default_factory=list)
    critical: list[str] = Field(default_factory=list)



class LoggingConfig(BaseModel):
    log_dir: Path = Path("./logs")
    json_access: bool = False

    terminal: TerminalLogConfig = Field(
        default_factory=TerminalLogConfig
    )

    app_file: LogFileConfig = Field(
        default_factory=lambda: LogFileConfig(
            enabled=True,
            filename="app.log",
            level="DEBUG",
        )
    )

    debug_file: LogFileConfig = Field(
        default_factory=lambda: LogFileConfig(
            enabled=True,
            filename="debug.log",
            level="DEBUG",
        )
    )

    access_file: LogFileConfig = Field(
        default_factory=lambda: LogFileConfig(
            enabled=True,
            filename="http_access.log",
            level="INFO",
        )
    )

    noise: NoiseFilterConfig = Field(
        default_factory=NoiseFilterConfig
    )

    third_party: ThirdPartyLogConfig = Field(
        default_factory=ThirdPartyLogConfig
    )
