"""LLM providers with transient-failure retry."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, Optional, Type

from loguru import logger

from pai.observability.tracer import get_tracer

from .models import (
    LLMMessage,
    LLMProviderConfig,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMProviderError(RuntimeError):
    """Base exception for provider failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when provider configuration is invalid."""


class LLMAuthenticationError(LLMProviderError):
    """Raised when provider authentication fails."""


# ---------------------------------------------------------------------------
# Transient-failure detection
# ---------------------------------------------------------------------------
#
# Split into two lists so the decision is auditable — a failed auth (401)
# must never be retried, and a 429 rate limit definitely should be.

_TRANSIENT_MARKERS = (
    "503", "502", "500", "504",         # HTTP 5xx from any provider
    "unavailable", "overloaded",        # Gemini / Anthropic phrasing
    "rate limit", "429",                # OpenAI, OpenRouter
    "timed out", "timeout",             # Network / client
    "connection", "reset", "broken pipe",  # Socket-level
    "temporarily",
)

_PERMANENT_MARKERS = (
    "401", "403",                       # Auth
    "unauthorized", "forbidden", "invalid api key",
    "404", "not found",                 # Model / resource
    "400", "invalid request", "bad request",   # Malformed
    "quota exceeded",                   # Billing — retrying makes it worse
)


def _is_transient(exc: Exception) -> bool:
    """
    Return True if `exc` is a transient failure the caller should retry.

    Permanent errors (auth, quota, malformed request) return False —
    retrying them wastes API quota and delays the user-visible error.
    """
    msg = str(exc).lower()
    name = type(exc).__name__.lower()

    # Explicit permanent wins — a 401 that happens to say "connection"
    # in a stack trace shouldn't be retried
    if any(marker in msg for marker in _PERMANENT_MARKERS):
        return False

    if any(marker in msg for marker in _TRANSIENT_MARKERS):
        return True

    # Class-name fallback — libraries like `openai` expose `RateLimitError`
    # without necessarily putting "429" in `str(exc)`
    if any(
        kw in name
        for kw in ("ratelimit", "timeout", "connection", "unavailable", "servererror")
    ):
        return True

    return False


# ---------------------------------------------------------------------------
# Retry helper (module-level, reused by every provider)
# ---------------------------------------------------------------------------

async def _retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    *,
    provider: str,
    operation: str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """
    Call `await fn()` with exponential backoff on transient failures.

    Retry policy:
      - attempt 1 → immediate
      - attempt 2 → base_delay * 1
      - attempt 3 → base_delay * 2
      - attempt 4 → base_delay * 4 (if max_attempts > 3)

    Non-transient failures (auth, malformed request) raise immediately —
    retrying them can't succeed and delays surfacing the real problem.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc

            if attempt == max_attempts:
                # Last attempt — surface the original error
                raise
            if not _is_transient(exc):
                # Permanent — retrying won't help
                raise

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "{} {} transient failure (attempt {}/{}), "
                "retrying in {:.1f}s: {}",
                provider, operation, attempt, max_attempts, delay, exc,
            )
            await asyncio.sleep(delay)

    # Unreachable — the loop either returns or raises — but keeps
    # type-checkers happy
    if last_exc is not None:
        raise last_exc


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract interface implemented by every LLM provider."""

    name: str = "unknown"

    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self.api_key = config.api_key
        self.model = config.model_name or self.default_model
        self.timeout = config.timeout

        # Read from config if present, else default to 3
        self.max_attempts = int(
            getattr(config, "max_retries", 3) or 3
        )

        if not self.api_key:
            raise LLMConfigurationError(
                f"API key missing for provider '{self.name}'."
            )

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Default model used by this provider."""
        ...

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response from the provider."""
        ...

    async def health_check(self) -> bool:
        """
        Basic provider health check.

        Providers can override this with a cheaper API-specific check.
        """
        try:
            request = LLMRequest(
                messages=[LLMMessage(role="user", content="ping")],
                max_tokens=1,
            )
            await self.generate(request)
            return True
        except Exception:
            return False

    def _model_for_request(self, request: LLMRequest) -> str:
        return request.model or self.model

    async def _call_with_retry(
        self,
        fn: Callable[[], Awaitable[Any]],
        operation: str,
    ) -> Any:
        """
        Instance-scoped retry helper. Every provider wraps its API call
        in this so transient retry behaviour is uniform.
        """
        return await _retry_with_backoff(
            fn,
            provider=self.name,
            operation=operation,
            max_attempts=self.max_attempts,
        )

    # ------------------------------------------------------------------
    # Tracing helpers
    # ------------------------------------------------------------------

    def _prompt_for_trace(self, request: LLMRequest) -> str:
        parts = []
        for m in request.messages:
            parts.append(f"{m.role.upper()}: {m.content}")
        return "\n\n".join(parts)

    def _trace_llm(
        self,
        *,
        model: str,
        prompt: str,
        response: Optional[str] = None,
        usage: Optional[LLMUsage] = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        finish_reason: Optional[str] = None,
        retries: Optional[int] = None,
    ) -> None:
        tracer = get_tracer()
        extra: Dict[str, Any] = {}
        if usage is not None:
            extra["usage"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
        if finish_reason:
            extra["finish_reason"] = finish_reason
        if retries:
            extra["retries"] = retries

        tracer.log_llm_call(
            provider=self.name,
            model=model,
            prompt=prompt,
            response=response,
            error=error,
            duration_ms=duration_ms,
            extra=extra or None,
        )


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

        client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)
        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)

        self._trace_llm(model=model, prompt=prompt)

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": m.role, "content": m.content}
                        for m in request.messages
                    ],
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    **request.extra,
                ),
                operation="chat.completions",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=prompt,
                error=str(exc), duration_ms=duration_ms,
            )
            raise LLMProviderError(f"OpenAI request failed: {exc}") from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        choice = response.choices[0]

        usage = None
        if response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

        content = choice.message.content or ""
        self._trace_llm(
            model=model, prompt=prompt,
            response=content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=choice.finish_reason,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.finish_reason,
            metadata={"id": response.id},
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
        trace_prompt = self._prompt_for_trace(request)

        config: Dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        config.update(request.extra)

        self._trace_llm(model=model, prompt=trace_prompt)

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                # Wrap the blocking client call in to_thread, then
                # re-wrap for retries — the retry helper awaits the
                # whole thunk
                lambda: asyncio.to_thread(
                    client.models.generate_content,
                    model=model,
                    contents=prompt,
                    config=config,
                ),
                operation="models.generate_content",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=trace_prompt,
                error=str(exc), duration_ms=duration_ms,
            )
            raise LLMProviderError(f"Gemini request failed: {exc}") from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        content = response.text or ""
        self._trace_llm(
            model=model, prompt=trace_prompt,
            response=content, duration_ms=duration_ms,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            metadata={},
        )

    @staticmethod
    def _convert_messages(messages: list[LLMMessage]) -> str:
        """Flatten the common message list into a Gemini prompt string."""
        parts = []
        for m in messages:
            parts.append(f"{m.role.upper()}: {m.content}")
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
            raise LLMProviderError("httpx is required for OpenRouter.") from exc

        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        payload.update(request.extra)

        self._trace_llm(model=model, prompt=prompt)

        async def _call():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                _call, operation="chat.completions",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=prompt,
                error=str(exc), duration_ms=duration_ms,
            )
            raise LLMProviderError(f"OpenRouter request failed: {exc}") from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        data = response.json()
        choice = data["choices"][0]

        raw_content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
        usage = None
        if data.get("usage"):
            u = data["usage"]
            usage = LLMUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )
        

        if raw_content is None:
            # Reasoning models consume the budget on hidden reasoning and
            # return null content when max_tokens is hit. Surface this clearly
            # instead of silently sending an empty reply downstream.
            logger.warning(
                "OpenRouter returned null content "
                "(model={}, finish_reason={}). Increase max_tokens or use "
                "a non-reasoning model.",
                model, finish_reason,
            )
            raw_content = ""

        content = raw_content
        
        self._trace_llm(
            model=model, prompt=prompt,
            response=content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=choice.get("finish_reason"),
        )

        logger.info("OpenRouter response: {}", content)

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            metadata={"id": data.get("id")},
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
            raise LLMProviderError("httpx is required for Kimi.") from exc

        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        payload.update(request.extra)

        self._trace_llm(model=model, prompt=prompt)

        async def _call():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    "https://api.moonshot.ai/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                return r

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                _call, operation="chat.completions",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=prompt,
                error=str(exc), duration_ms=duration_ms,
            )
            raise LLMProviderError(f"Kimi request failed: {exc}") from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        data = response.json()
        choice = data["choices"][0]

        usage = None
        if data.get("usage"):
            u = data["usage"]
            usage = LLMUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )

        content = choice["message"]["content"]
        self._trace_llm(
            model=model, prompt=prompt,
            response=content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=choice.get("finish_reason"),
        )

        logger.info(f"Kimi response: {content}, usage: {usage}, finish_reason: {choice.get('finish_reason')}, request_id: {data.get('id')}")

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            metadata={"id": data.get("id")},
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROVIDER_CLASSES: Dict[str, Type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
    "kimi": KimiProvider,
}


def create_provider(config: LLMProviderConfig) -> LLMProvider:
    """Create an LLM provider from config."""
    return PROVIDER_CLASSES[config.provider](config)