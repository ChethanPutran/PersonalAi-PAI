import os
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

# Optional: load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ----------------------------------------------------------------------
#  Abstract base class
# ----------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """Abstract interface for all LLM providers."""

    def __init__(self, api_key: Optional[str] = None, model: str = None, **kwargs):
        self.api_key = api_key or self._get_default_api_key()
        self.model = model or self._get_default_model()
        self.extra_kwargs = kwargs

    @abstractmethod
    def _get_default_api_key(self) -> str:
        """Return the default API key for this provider (from env)."""
        pass

    @abstractmethod
    def _get_default_model(self) -> str:
        """Return the default model name for this provider."""
        pass

    @abstractmethod
    async def complete(self, prompt: str, **kwargs) -> str:
        """Send a prompt to the LLM and return the response text."""
        pass


# ----------------------------------------------------------------------
#  Concrete providers
# ----------------------------------------------------------------------

class OpenAIProvider(BaseLLMProvider):
    def _get_default_api_key(self) -> str:
        return os.environ.get("OPENAI_API_KEY", "")

    def _get_default_model(self) -> str:
        return "gpt-3.5-turbo"

    async def complete(self, prompt: str, **kwargs) -> str:
        from openai import OpenAI

        if not self.api_key:
            raise ValueError("OpenAI API key missing.")
        client = OpenAI(api_key=self.api_key)
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


class GeminiProvider(BaseLLMProvider):
    def _get_default_api_key(self) -> str:
        return os.environ.get("GEMINI_API_KEY", "")

    def _get_default_model(self) -> str:
        return "gemini-1.5-flash"

    async def complete(self, prompt: str, **kwargs) -> str:
        import google.generativeai as genai

        if not self.api_key:
            raise ValueError("Gemini API key missing.")
        genai.configure(api_key=self.api_key)
        model = kwargs.get("model", self.model)
        # Gemini does not support temperature/max_tokens in the same way,
        # but we can pass generation_config
        generation_config = {
            "temperature": kwargs.get("temperature", 0.7),
            "max_output_tokens": kwargs.get("max_tokens", 1000),
        }
        model_obj = genai.GenerativeModel(model)
        response = model_obj.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(**generation_config)
        )
        return response.text


class OpenRouterProvider(BaseLLMProvider):
    def _get_default_api_key(self) -> str:
        return os.environ.get("OPENROUTER_API_KEY", "")

    def _get_default_model(self) -> str:
        return "openai/gpt-3.5-turbo"

    async def complete(self, prompt: str, **kwargs) -> str:
        import requests

        if not self.api_key:
            raise ValueError("OpenRouter API key missing.")
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


class KimiProvider(BaseLLMProvider):
    def _get_default_api_key(self) -> str:
        return os.environ.get("KIMI_API_KEY", "")

    def _get_default_model(self) -> str:
        return "moonshot-v1-8k"

    async def complete(self, prompt: str, **kwargs) -> str:
        import requests

        if not self.api_key:
            raise ValueError("Kimi API key missing.")
        model = kwargs.get("model", self.model)
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


# ----------------------------------------------------------------------
#  Factory / unified entry point
# ----------------------------------------------------------------------

class LLMProvider:
    """
    Unified interface that holds a selected provider instance.
    Usage:
        provider = LLMProvider(provider_name='openai')
        response = provider.complete('Hello')
        # or change provider later:
        provider.set_provider('gemini')
    """
    PROVIDER_MAP = {
        'openai': OpenAIProvider,
        'gemini': GeminiProvider,
        'openrouter': OpenRouterProvider,
        'kimi': KimiProvider,
    }

    def __init__(self, provider_name: str = 'openai', **kwargs):
        self._provider: Optional[BaseLLMProvider] = None
        self.set_provider(provider_name, **kwargs)

    def set_provider(self, provider_name: str, **kwargs) -> None:
        """Change the underlying provider and re‑initialize with optional overrides."""
        if provider_name not in self.PROVIDER_MAP:
            raise ValueError(f"Unknown provider: {provider_name}. Available: {list(self.PROVIDER_MAP.keys())}")
        ProviderClass = self.PROVIDER_MAP[provider_name]
        self._provider = ProviderClass(**kwargs)

    async def complete(self, prompt: str, **kwargs) -> str:
        if self._provider is None:
            raise RuntimeError("No provider selected.")
        return await self._provider.complete(prompt, **kwargs)


# ----------------------------------------------------------------------
#  Example usage
# ----------------------------------------------------------------------

if __name__ == "__main__":
    # Example 1: Interactive
    import sys

    def interactive():
        print("Available providers:", list(LLMProvider.PROVIDER_MAP.keys()))
        prov = input("Choose provider (default: openai): ").strip() or 'openai'
        provider = LLMProvider(prov)
        print(f"Using {prov}. Type 'exit' to quit.\n")
        while True:
            prompt = input("Prompt> ")
            if prompt.lower() in ('exit', 'quit'):
                break
            try:
                response = provider.complete(prompt)
                print("Response:", response, "\n")
            except Exception as e:
                print(f"Error: {e}\n")

    # If run directly, start interactive session
    interactive()