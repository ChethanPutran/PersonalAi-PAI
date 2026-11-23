
from enum import Enum


class LLMProviderName(Enum):
    """Enumeration of supported LLM providers."""
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    KIMI = "kimi"
    LOCAL = "local"

class LLMModel(Enum):
    """Enumeration of supported LLM models."""
    GEMINI_3_5_FLASH = "gemini-3.5-flash"
    GEMINI_4_0 = "gemini-4.0"
    GPT_3_5_TURBO = "gpt-3.5-turbo"
    GPT_4 = "gpt-4"
    GPT_4_32K = "gpt-4-32k"
    LOCAL_MODEL = "local-model"
    OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

