
from enum import Enum, StrEnum


class LLMProviderName(StrEnum):
    """Enumeration of supported LLM providers."""
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    AIROUTER = "airouter"
    KIMI = "kimi"
    LOCAL = "local"

class LLMModel(StrEnum):
    """Enumeration of supported LLM models."""
    GEMINI_3_5_FLASH = "gemini-3.5-flash"
    GEMINI_4_0 = "gemini-4.0"
    GPT_3_5_TURBO = "gpt-3.5-turbo"
    GPT_4 = "gpt-4"
    GPT_4_32K = "gpt-4-32k"
    LOCAL_MODEL = "local-model"
    OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

    @classmethod
    def _missing_(cls, value):
        """Gateways (OpenRouter, AIRouter) serve hundreds of models that change weekly, so any non-empty
        model id is accepted and behaves like a member: a real str whose `.value`/`.name`/str()/f-string/JSON
        form is the id itself (equal to, and hashing like, the plain string)."""
        if isinstance(value, str) and value.strip():
            return _dynamic_model(cls, value.strip())
        return None


_DYNAMIC: dict = {}


def _dynamic_model(cls, value: str):
    member = _DYNAMIC.get(value)
    if member is None:
        member = str.__new__(cls, value)
        member._name_, member._value_ = value, value
        _DYNAMIC[value] = member
    return member
