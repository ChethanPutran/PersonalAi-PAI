from __future__ import annotations
from enum import StrEnum


class RuntimeKind(StrEnum):
    """Where the plugin executes — never what it's written in."""
    SERVER = "server"
    DEVICE = "device"


class PluginLanguage(StrEnum):
    """How the plugin is implemented."""
    NATIVE   = "native"       # C/C++/Rust .so / .dylib / .dll
    KOTLIN   = "kotlin"       # Android .dex
    SWIFT    = "swift"        # iOS
    PYTHON   = "python"       # server-side
    JAVASCRIPT = "javascript"



class PreconditionType(StrEnum):
    PATH_IN_ROOTS = "pathInRoots"
    COMMAND_PREFIX = "commandPrefix"
    ENV_PRESENT = "envPresent"
    CONSENT_FLAG = "consentFlag"