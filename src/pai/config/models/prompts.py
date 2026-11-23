from enum import Enum
from typing import Dict

from pydantic import BaseModel


class PromptRegistryConfig(BaseModel):
    root: str = "./prompts"
    frozen: bool = True
    override_versions: Dict[str, str] = {}

class PromptGuardMode(Enum):
    OFF = "off"
    WARN = "warn"
    SANITIZE = "sanitize"
    REJECT = "reject"
    
class PromptsGuardConfig(BaseModel):
    request_mode: PromptGuardMode = PromptGuardMode.WARN
    response_mode: PromptGuardMode = PromptGuardMode.SANITIZE
    fallback_reply: str = ("Okay — I've handled that for you. Let me know if you'd like "
            "anything else.")
    redact_secrets: bool = True
    strip_tool_calls: bool = True
    detect_jailbreak_success: bool = True
    
class PromptsConfig(BaseModel):
    registry: PromptRegistryConfig = PromptRegistryConfig()
    track_usage: bool = True
    guard_enabled: bool = True
    guard: PromptsGuardConfig = PromptsGuardConfig()