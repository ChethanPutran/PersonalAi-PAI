from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from .models import (
    LLMMessage,
    LLMProviderConfig,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)


class LLMProviderError(RuntimeError):
    """Base exception for provider failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when provider configuration is invalid."""


class LLMAuthenticationError(LLMProviderError):
    """Raised when provider authentication fails."""


class LLMProvider(ABC):
    """
    Abstract interface implemented by every LLM provider.
    """

    name: str = "unknown"

    def __init__(self, config: LLMProviderConfig):
        self.config = config

        self.api_key = config.api_key
        self.model = config.model or self.default_model
        self.timeout = config.timeout

        if not self.api_key:
            raise LLMConfigurationError(
                f"API key missing for provider '{self.name}'."
            )

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Default model used by this provider."""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response from the provider."""

    async def health_check(self) -> bool:
        """
        Basic provider health check.

        Providers can override this with a cheaper API-specific check.
        """
        try:
            request = LLMRequest(
                messages=[
                    LLMMessage(
                        role="user",
                        content="ping",
                    )
                ],
                max_tokens=1,
            )

            await self.generate(request)
            return True

        except Exception:
            return False

    def _model_for_request(self, request: LLMRequest) -> str:
        return request.model or self.model


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    name = "openai"

    @property
    def default_model(self) -> str:
        return "gpt-4o-mini"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMProviderError(
                "OpenAI package is not installed. "
                "Install it with: pip install openai"
            ) from exc

        client = AsyncOpenAI(
            api_key=self.api_key,
            timeout=self.timeout,
        )

        model = self._model_for_request(request)

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in request.messages
                ],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                **request.extra,
            )

        except Exception as exc:
            raise LLMProviderError(
                f"OpenAI request failed: {exc}"
            ) from exc

        choice = response.choices[0]

        usage = None

        if response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

        return LLMResponse(
            content=choice.message.content or "",
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.finish_reason,
            metadata={
                "id": response.id,
            },
        )


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    name = "gemini"

    @property
    def default_model(self) -> str:
        return "gemini-2.5-flash"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMProviderError(
                "Google GenAI package is not installed. "
                "Install it with: pip install google-genai"
            ) from exc

        model = self._model_for_request(request)

        client = genai.Client(api_key=self.api_key)

        prompt = self._convert_messages(request.messages)

        config: Dict[str, Any] = {
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens

        config.update(request.extra)

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=prompt,
                config=config,
            )

        except Exception as exc:
            raise LLMProviderError(
                f"Gemini request failed: {exc}"
            ) from exc

        return LLMResponse(
            content=response.text or "",
            provider=self.name,
            model=model,
            metadata={},
        )

    @staticmethod
    def _convert_messages(messages: list[LLMMessage]) -> str:
        """
        Convert the common message representation into a Gemini-compatible
        prompt.

        This keeps the rest of PAI provider-independent.
        """

        parts = []

        for message in messages:
            parts.append(
                f"{message.role.upper()}: {message.content}"
            )

        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

class OpenRouterProvider(LLMProvider):
    name = "openrouter"

    @property
    def default_model(self) -> str:
        return "openai/gpt-4o-mini"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError as exc:
            raise LLMProviderError(
                "httpx is required for OpenRouter."
            ) from exc

        model = self._model_for_request(request)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        payload.update(request.extra)

        async with httpx.AsyncClient(
            timeout=self.timeout
        ) as client:

            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )

                response.raise_for_status()

            except Exception as exc:
                raise LLMProviderError(
                    f"OpenRouter request failed: {exc}"
                ) from exc

        data = response.json()

        choice = data["choices"][0]

        usage_data = data.get("usage")

        usage = None

        if usage_data:
            usage = LLMUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get(
                    "completion_tokens",
                    0,
                ),
                total_tokens=usage_data.get(
                    "total_tokens",
                    0,
                ),
            )

        return LLMResponse(
            content=choice["message"]["content"],
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            metadata={
                "id": data.get("id"),
            },
        )


# ---------------------------------------------------------------------------
# Kimi / Moonshot
# ---------------------------------------------------------------------------

class KimiProvider(LLMProvider):
    name = "kimi"

    @property
    def default_model(self) -> str:
        return "moonshot-v1-8k"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            import httpx
        except ImportError as exc:
            raise LLMProviderError(
                "httpx is required for Kimi."
            ) from exc

        model = self._model_for_request(request)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in request.messages
            ],
            "temperature": request.temperature,
        }

        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        payload.update(request.extra)

        async with httpx.AsyncClient(
            timeout=self.timeout
        ) as client:

            try:
                response = await client.post(
                    "https://api.moonshot.cn/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )

                response.raise_for_status()

            except Exception as exc:
                raise LLMProviderError(
                    f"Kimi request failed: {exc}"
                ) from exc

        data = response.json()

        choice = data["choices"][0]

        usage_data = data.get("usage")

        usage = None

        if usage_data:
            usage = LLMUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get(
                    "completion_tokens",
                    0,
                ),
                total_tokens=usage_data.get(
                    "total_tokens",
                    0,
                ),
            )

        return LLMResponse(
            content=choice["message"]["content"],
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            metadata={
                "id": data.get("id"),
            },
        )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

PROVIDER_CLASSES: Dict[str, Type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "kimi": KimiProvider,
}


def create_provider(
    name: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
    base_url: Optional[str] = None,
    **extra: Any,
) -> LLMProvider:
    """
    Create an LLM provider.

    API keys are read from environment variables when not explicitly
    supplied.
    """

    name = name.lower().strip()

    if name not in PROVIDER_CLASSES:
        raise ValueError(
            f"Unknown LLM provider '{name}'. "
            f"Available providers: {sorted(PROVIDER_CLASSES)}"
        )

    env_keys = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "kimi": "KIMI_API_KEY",
    }

    api_key = api_key or os.getenv(env_keys[name])

    config = LLMProviderConfig(
        name=name,
        api_key=api_key,
        model=model,
        timeout=timeout,
        base_url=base_url,
        extra=extra,
    )

    return PROVIDER_CLASSES[name](config)