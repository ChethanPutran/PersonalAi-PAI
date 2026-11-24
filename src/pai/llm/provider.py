"""LLM providers with transient-failure retry."""
from __future__ import annotations

import asyncio
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    AsyncIterator,
    Dict,
    Optional,
    Type,
    cast,
)

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from pai.common.enums.llm import LLMModel, LLMProviderName
from pai.config.models.llm import LLMProviderConfig
from pai.observability.tracer import get_tracer

from pai.observability.tracer import current_trace_id
from .breakdown import current_breakdown, scale_to
from .cost import CostTracker, PriceBook, as_cost, compute_cost, COST_CONFIGURED, COST_PROVIDER, current_route
from .models import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
)

if TYPE_CHECKING:
    from google.genai.types import GenerateContentConfigOrDict


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMProviderError(RuntimeError):
    """Base exception for provider failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when provider configuration is invalid (bad key, empty
    request, unknown provider name). Never retried."""


class LLMAuthenticationError(LLMProviderError):
    """Raised when provider authentication fails (401/403). Never retried.

    Kept distinct from `LLMConfigurationError` so callers that want to
    distinguish "bad config" from "auth expired" can — e.g. a UI can
    prompt for a fresh API key on this exception specifically.
    """


# ---------------------------------------------------------------------------
# Transient-failure detection
# ---------------------------------------------------------------------------
#
# Two lists so the decision is auditable: a failed auth (401) must never
# be retried, and a 429 rate limit definitely should be. Permanent
# markers are checked first — an exception whose traceback happens to
# contain the word "connection" but whose primary failure was a 401
# must still be treated as permanent.

_TRANSIENT_MARKERS = (
    "503", "502", "500", "504",         # HTTP 5xx from any provider
    "unavailable", "overloaded",        # Gemini / Anthropic phrasing
    "rate limit", "429",                # OpenAI, OpenRouter
    "timed out", "timeout",             # Network / client
    "connection", "reset", "broken pipe",  # Socket-level
    "temporarily",
    "internal server error",            # httpx body of a 500
)

_PERMANENT_MARKERS = (
    "401", "403",                       # Auth
    "unauthorized", "forbidden", "invalid api key",
    "404", "not found",                 # Model / resource
    "400", "invalid request", "bad request",   # Malformed
    "quota exceeded",                   # Billing — retrying makes it worse
    "context_length_exceeded",          # Token limit — caller must shrink
)


# Keys the providers set explicitly. Anything in `request.extra` matching
# one of these is dropped before merging, so a caller that (accidentally)
# puts `temperature` or `max_tokens` in `extra` gets the provider's
# explicit value rather than a `TypeError` (OpenAI) or a silent override
# (httpx-based providers).
_RESERVED_KWARGS = frozenset({
    # OpenAI-compatible providers
    "messages", "model", "temperature", "max_tokens", "stream",
    # Gemini
    "contents", "system_instruction", "max_output_tokens",
})


def _extra_kwargs(request: LLMRequest) -> Dict[str, Any]:
    """Return `request.extra` minus keys that would collide with
    explicitly-passed provider args."""
    return {
        k: v for k, v in (request.extra or {}).items()
        if k not in _RESERVED_KWARGS
    }


def _model_id(model: Any) -> str:
    """Wire id of a model (enum -> its value, anything else -> str)."""
    return str(getattr(model, "value", model))


# ---------------------------------------------------------------------------
# Streaming support
# ---------------------------------------------------------------------------
#
# OpenAI-compatible chat-completion chunks (OpenAI SDK events, OpenRouter and Kimi SSE
# `data:` payloads) are parsed into these models once, at the wire boundary.

class _OAUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: Optional[int] = 0
    completion_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    total_cost: Any = None          # AIRouter: cost of the call in USD
    cost: Any = None                # OpenRouter: same


class _OADelta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: Optional[str] = None            # may be null (role-only / reasoning chunks)


class _OAChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    delta: _OADelta = Field(default_factory=_OADelta)
    finish_reason: Optional[str] = None


class _OAChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choices: list[_OAChoice] = Field(default_factory=list)   # empty on the usage-only chunk
    usage: Optional[_OAUsage] = None


class _StreamError(BaseModel):
    """`data: {"error": {...}}` sent by gateways in the middle of a stream."""
    model_config = ConfigDict(extra="ignore")
    error: Any = None


@dataclass
class _RawEvent:
    """Normalized upstream event: some new text and/or a finish reason and/or usage."""
    text: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[LLMUsage] = None


_STREAM_END = object()


async def _aclose_quietly(it: Any) -> None:
    close = getattr(it, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:                      # never mask the real error
        logger.debug("stream close failed: {}", exc)


def _usage_from(u: Optional[_OAUsage]) -> Optional[LLMUsage]:
    if u is None:
        return None
    return LLMUsage(prompt_tokens=u.prompt_tokens or 0,
                    completion_tokens=u.completion_tokens or 0,
                    total_tokens=u.total_tokens or 0,
                    provider_cost_usd=as_cost(u.total_cost if u.total_cost is not None else u.cost))


def _events_from_oa_chunk(chunk: _OAChunk) -> list[_RawEvent]:
    events: list[_RawEvent] = []
    for choice in chunk.choices[:1]:
        if choice.delta.content or choice.finish_reason:
            events.append(_RawEvent(text=choice.delta.content or "",
                                    finish_reason=choice.finish_reason))
    if chunk.usage is not None:
        events.append(_RawEvent(usage=_usage_from(chunk.usage)))
    return events


def _is_transient(exc: Exception) -> bool:
    """
    Return True if `exc` is a transient failure the caller should retry.

    Order matters: permanent markers are checked first, so a 401 wrapped
    in a generic network error is still treated as permanent.
    """
    msg = str(exc).lower()
    name = type(exc).__name__.lower()

    if any(marker in msg for marker in _PERMANENT_MARKERS):
        return False

    if any(marker in msg for marker in _TRANSIENT_MARKERS):
        return True

    # Class-name fallback — SDKs like `openai` expose `RateLimitError`,
    # `APITimeoutError`, etc. without putting a code in `str(exc)`.
    if any(
        kw in name
        for kw in (
            "ratelimit", "timeout", "connection",
            "unavailable", "servererror",
        )
    ):
        return True

    return False


def _truncate_for_log(text: str, limit: int = 500) -> str:
    """Truncate a body for inclusion in an exception message. Newlines
    preserved so the operator sees the shape of the error."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"… (+{len(text) - limit} more)"


# ---------------------------------------------------------------------------
# Retry helper (module-level, reused by every provider)
# ---------------------------------------------------------------------------

async def _retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    *,
    provider: LLMProviderName,
    operation: str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    on_attempt: Optional[Callable[[int], None]] = None,
) -> Any:
    """
    Call `await fn()` with exponential backoff + jitter on transient failures.

    Retry policy:
      - attempt 1 → immediate
      - attempt 2 → ~1× base_delay
      - attempt 3 → ~2× base_delay
      - capped at `max_delay`, jittered by ±50% so that N concurrent
        requests hitting a 429 don't synchronize their retries and
        re-hammer the provider at the same instant.

    `on_attempt(n)` is called before each attempt so callers can record
    the actual retry count for tracing.

    Non-transient failures raise immediately — retrying a 401 or a 400
    only delays surfacing the real error and burns API quota.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc

            if attempt == max_attempts:
                raise
            if not _is_transient(exc):
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            # 0.5×–1.5× jitter. Without this, 100 simultaneous clients
            # retry at exactly t=1s, t=3s, t=7s — worst possible timing
            # when the provider is already saturated.
            delay *= 0.5 + random.random()

            logger.warning(
                "{} {} transient failure (attempt {}/{}), "
                "retrying in {:.1f}s: {}",
                provider, operation, attempt, max_attempts, delay, exc,
            )
            await asyncio.sleep(delay)

    if last_exc is not None:  # unreachable; satisfies type-checkers
        raise last_exc


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Abstract interface implemented by every LLM provider."""

    name: LLMProviderName 

    # Set by LLMRouter. Without them tokens are still traced, cost is just not computed/recorded.
    price_book: Optional[PriceBook] = None
    cost_tracker: Optional[CostTracker] = None

    def __init__(self, config: LLMProviderConfig):
        self.config = config
        self.api_key = config.api_key
        self.model = config.model_name or self.default_model
        self.timeout = config.timeout

        # `0 or 3` would coerce max_retries=0 back to 3 — a caller that
        # explicitly disables retries must get zero retries. Preserve
        # explicit 0 by distinguishing `None` (default) from `0`.
        raw = getattr(config, "max_retries", None)
        self.max_attempts = 3 if raw is None else max(1, int(raw))

        if not self.api_key:
            env_var = f"PAI_LLM_{str(self.name).upper().replace('-', '_')}_KEY"
            raise LLMConfigurationError(
                f"API key missing for provider '{self.name}'. "
                f"Set it via the config file or environment variable '{env_var}'."
            )

        # Retry-count tracking. `_call_with_retry` updates this before
        # each attempt; providers read `_last_attempts - 1` when calling
        # `_trace_llm(..., retries=...)` so the trace shows how many
        # retries the call actually needed.
        self._last_attempts: int = 1

        # Health-check result cache. A `/health` endpoint hitting this
        # on every probe would burn API quota at 86k calls/day at 1 Hz.
        self._health_cached_at: float = 0.0
        self._health_cached_ok: Optional[bool] = None

    # ------------------------------------------------------------------
    # Abstract surface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def default_model(self) -> str:
        ...

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Stream the completion as incremental chunks.

        Contract: yields chunks whose ``delta`` are appended to form the full reply; the
        final chunk has ``finish_reason``. Cancelling the consuming task must abort the
        upstream request. Providers without native streaming inherit this fallback, which
        yields the whole reply as a single chunk — so callers can always use ``stream``.
        """
        response = await self.generate(request)
        yield LLMStreamChunk(
            delta=response.content,
            finish_reason=response.finish_reason or "stop",
            usage=response.usage,
        )


    async def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        **extra: Any,
    ) -> str:
        """
        String-in / string-out convenience wrapper around `generate()`.

        Use when you only need the text content and don't care about
        token usage or finish_reason. For anything where truncation,
        metadata, or usage matters, call `generate()` directly and read
        the `LLMResponse` fields.

        `system` becomes a proper system message, not a prefix — providers
        that support a dedicated system slot (Gemini's `system_instruction`)
        receive it through `LLMRequest.messages`, which the Gemini provider
        splits out.
        """
        messages: list[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        messages.append(LLMMessage(role="user", content=prompt))

        response = await self.generate(LLMRequest(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra or {},
        ))
        return response.content

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _validate_request(self, request: LLMRequest) -> None:
        """Fail locally with a clear message instead of shipping an
        empty `messages` list to the provider and getting a cryptic 400."""
        if not request.messages:
            raise LLMConfigurationError(
                f"[{self.name}] LLMRequest.messages must not be empty"
            )

    async def _call_with_retry(
        self,
        fn: Callable[[], Awaitable[Any]],
        operation: str,
    ) -> Any:
        """Instance-scoped retry wrapper. Tracks the attempt count so
        `_trace_llm(retries=...)` can record it."""
        self._last_attempts = 1
        return await _retry_with_backoff(
            fn,
            provider=self.name,
            operation=operation,
            max_attempts=self.max_attempts,
            on_attempt=lambda n: setattr(self, "_last_attempts", n),
        )

    async def _stream_events(
        self,
        *,
        model: LLMModel,
        prompt: str,
        label: str,
        open_raw: Callable[[], AsyncIterator[_RawEvent]],
        operation: str,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Shared streaming engine.

        * Opening the stream AND receiving its first event is retried via
          ``_call_with_retry`` (a failure there means nothing was emitted yet).
        * After the first event, errors are never retried (that would replay output):
          they surface as LLMProviderError.
        * Consumer cancellation / early exit always closes the upstream stream.
        * Exactly one final chunk is emitted, carrying finish_reason (+usage if known).
        """
        self._trace_llm(model=model, prompt=prompt)
        t0 = time.perf_counter()
        raw: Any = None

        async def _attempt() -> tuple[Any, Any]:
            it = open_raw()
            try:
                first = await it.__anext__()
            except StopAsyncIteration:
                return it, _STREAM_END
            except BaseException:
                await _aclose_quietly(it)
                raise
            return it, first

        try:
            raw, first = await self._call_with_retry(_attempt, operation=operation)
        except Exception as exc:
            self._trace_llm(model=model, prompt=prompt, error=str(exc),
                            duration_ms=(time.perf_counter() - t0) * 1000,
                            retries=self._retries_so_far)
            raise LLMProviderError(f"{label} request failed: {exc}") from exc

        retries = self._retries_so_far
        text_parts: list[str] = []
        finish_reason: Optional[str] = None
        usage: Optional[LLMUsage] = None
        ttft_ms: Optional[float] = None
        completed = False
        try:
            event: Any = first
            while event is not _STREAM_END:
                if event.text:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000
                    text_parts.append(event.text)
                    yield LLMStreamChunk(delta=event.text)
                if event.finish_reason:
                    finish_reason = event.finish_reason
                if event.usage is not None:
                    usage = event.usage
                try:
                    event = await raw.__anext__()
                except StopAsyncIteration:
                    event = _STREAM_END
                except Exception as exc:          # mid-stream: surface, never replay
                    self._trace_llm(model=model, prompt=prompt,
                                    response="".join(text_parts), error=str(exc),
                                    duration_ms=(time.perf_counter() - t0) * 1000,
                                    retries=retries, ttft_ms=ttft_ms)
                    raise LLMProviderError(f"{label} stream failed: {exc}") from exc
            completed = True
            finish_reason = finish_reason or "stop"
            self._trace_llm(model=model, prompt=prompt, response="".join(text_parts),
                            usage=usage, duration_ms=(time.perf_counter() - t0) * 1000,
                            finish_reason=finish_reason, retries=retries, ttft_ms=ttft_ms)
            yield LLMStreamChunk(delta="", finish_reason=finish_reason, usage=usage)
        finally:
            if not completed:
                logger.debug("{} stream closed early after {} chars", label,
                             sum(len(p) for p in text_parts))
            await _aclose_quietly(raw)

    @property
    def _retries_so_far(self) -> int:
        """How many retries the last `_call_with_retry` needed (0 = none)."""
        return max(0, self._last_attempts - 1)

    def _model_for_request(self, request: LLMRequest) -> LLMModel:
        return request.model or self.model

    async def health_check(
        self,
        *,
        cache_ttl: float = 60.0,
    ) -> bool:
        """Basic provider health check, cached for `cache_ttl` seconds.

        The base implementation issues a real 1-token call — expensive
        if invoked per probe. Subclasses can override with a cheaper
        provider-specific check (e.g. a models-list endpoint).
        """
        now = time.time()
        if (
            self._health_cached_ok is not None
            and (now - self._health_cached_at) < cache_ttl
        ):
            return self._health_cached_ok

        try:
            await self.generate(LLMRequest(
                messages=[LLMMessage(role="user", content="ping")],
                max_tokens=1,
            ))
            ok = True
        except Exception as exc:
            logger.debug("{} health check failed: {}", self.name, exc)
            ok = False

        self._health_cached_ok = ok
        self._health_cached_at = now
        return ok

    async def aclose(self) -> None:
        """Release any cached clients. Base is a no-op; providers that
        hold long-lived HTTP clients override this.

        Call from the app's shutdown hook — otherwise sockets leak when
        the process exits normally instead of being killed.
        """
        return None

    # ------------------------------------------------------------------
    # Tracing helpers
    # ------------------------------------------------------------------

    def _prompt_for_trace(self, request: LLMRequest) -> str:
        return "\n\n".join(
            f"{m.role.upper()}: {m.content}" for m in request.messages
        )

    def _trace_llm(
        self,
        *,
        model: LLMModel,
        prompt: str,
        response: Optional[str] = None,
        usage: Optional[LLMUsage] = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        finish_reason: Optional[str] = None,
        retries: Optional[int] = None,
        ttft_ms: Optional[float] = None,
    ) -> None:
        tracer = get_tracer()
        extra: Dict[str, Any] = {}
        if usage is not None:
            self._account(model, usage)
            extra["usage"] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
        if finish_reason:
            extra["finish_reason"] = finish_reason
            if finish_reason == "length":
                # Truncated by max_tokens. Surface at WARN so operators
                # can raise the limit before users see cut-off replies.
                logger.warning(
                    "{} response truncated by max_tokens (model={})",
                    self.name, model,
                )
        # `retries=0` is meaningful (a first-try success) — record it
        # when the value is available rather than only on >0.
        if retries is not None:
            extra["retries"] = retries
        if ttft_ms is not None:                  # streaming: time to first token
            extra["ttft_ms"] = round(ttft_ms, 1)

        tracer.log_llm_call(
            provider=self.name,
            model=model,
            prompt=prompt,
            response=response,
            error=error,
            duration_ms=duration_ms,
            extra=extra or None,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            cost_usd=usage.cost_usd if usage else None,
            cost_source=usage.cost_source if usage else None,
            route=current_route.get(),
            prompt_breakdown=usage.breakdown if usage else None,
        )

    def _account(self, model: Any, usage: LLMUsage) -> None:
        """Price one finished call and add it to the running totals (exactly once per call).

        Price: configured per-million rates win (explicit operator choice); else what the gateway
        reported; else unknown (None, never a made-up 0). Never raises: accounting must not break a call.
        """
        if usage.recorded:
            return
        try:
            price = self.price_book.price_for(model) if self.price_book else None
            if price is not None:
                usage.cost_usd = compute_cost(usage.prompt_tokens, usage.completion_tokens, price)
                usage.cost_source = COST_CONFIGURED
            elif usage.provider_cost_usd is not None:
                usage.cost_usd = usage.provider_cost_usd
                usage.cost_source = COST_PROVIDER
            usage.breakdown = scale_to(usage.prompt_tokens, current_breakdown.get() or {}) or None
            usage.recorded = True
            if self.cost_tracker is not None:
                self.cost_tracker.record(
                    provider=self.name, model=model, prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens, total_tokens=usage.total_tokens,
                    cost_usd=usage.cost_usd, cost_source=usage.cost_source, breakdown=usage.breakdown,
                    trace_id=current_trace_id())
        except Exception as exc:
            logger.warning("LLM cost accounting failed: {}", exc)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    name = LLMProviderName.OPENAI

    def __init__(self, config: LLMProviderConfig):
        super().__init__(config)
        # Lazily-created client. One per provider instance so the
        # underlying connection pool is reused across requests —
        # creating a fresh AsyncOpenAI per call added a TLS handshake
        # for every plan-decompose, every chat-reply, every health check.
        self._client = None

    @property
    def default_model(self) -> str:
        return "gpt-4o-mini"

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.api_key, timeout=self.timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except AttributeError:
                # Older `openai` SDK versions expose `close` only as sync
                try:
                    self._client.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("OpenAI client close failed: {}", exc)
            self._client = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)

        try:
            from openai import AsyncOpenAI  # noqa: F401  (import check only)
        except ImportError as exc:
            raise LLMProviderError(
                "OpenAI package is not installed. "
                "Install it with: pip install openai"
            ) from exc

        client = self._get_client()
        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)

        self._trace_llm(model=model, prompt=prompt)

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                lambda: client.chat.completions.create(
                    model=_model_id(model),
                    messages=[
                        {"role": m.role, "content": m.content}
                        for m in request.messages
                    ], # type: ignore
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    **_extra_kwargs(request),
                ), # type: ignore
                operation="chat.completions",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=prompt,
                error=str(exc), duration_ms=duration_ms,
                retries=self._retries_so_far,
            )
            raise LLMProviderError(
                f"OpenAI request failed: {exc}"
            ) from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        choice = response.choices[0]

        usage = None
        if response.usage:
            usage = LLMUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

        content = (choice.message.content or "") if choice.message else ""
        self._trace_llm(
            model=model, prompt=prompt,
            response=content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=choice.finish_reason,
            retries=self._retries_so_far,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=choice.finish_reason,
            metadata={"id": response.id},
        )


    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self._validate_request(request)
        try:
            from openai import AsyncOpenAI  # noqa: F401  (import check only)
        except ImportError as exc:
            raise LLMProviderError(
                "OpenAI package is not installed. Install it with: pip install openai"
            ) from exc

        client = self._get_client()
        model = self._model_for_request(request)
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        extra = _extra_kwargs(request)

        async def _raw() -> AsyncIterator[_RawEvent]:
            kwargs: Dict[str, Any] = dict(
                model=_model_id(model), messages=messages, temperature=request.temperature,
                max_tokens=request.max_tokens, stream=True, **extra,
            )
            try:
                sdk_stream = await client.chat.completions.create(
                    stream_options={"include_usage": True}, **kwargs)
            except TypeError:                      # SDK/endpoint without stream_options
                sdk_stream = await client.chat.completions.create(**kwargs)
            try:
                async for ev in sdk_stream:
                    for out in _events_from_oa_chunk(_OAChunk.model_validate(ev.model_dump())):
                        yield out
            finally:
                close = getattr(sdk_stream, "close", None)
                if close is not None:
                    res = close()
                    if hasattr(res, "__await__"):
                        await res

        async for chunk in self._stream_events(
            model=model, prompt=self._prompt_for_trace(request), label="OpenAI",
            open_raw=_raw, operation="chat.completions.stream",
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    name = LLMProviderName.GEMINI

    def __init__(self, config: LLMProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def default_model(self) -> str:
        return "gemini-2.5-flash"

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            # google-genai exposes a sync `close()` on newer versions.
            # No async variant as of the SDK this was written against.
            try:
                close = getattr(self._client, "close", None)
                if callable(close):
                    close()
            except Exception as exc:
                logger.debug("Gemini client close failed: {}", exc)
            self._client = None

    @staticmethod
    def _split_system_and_body(
        messages: list[LLMMessage],
    ) -> tuple[str, str]:
        """
        Gemini has a dedicated `system_instruction` config slot, and
        treating `system` messages as plain text-prefixed user turns
        weakens them. The decomposer relies heavily on the system
        prompt's numbered rules — keep it as a proper system
        instruction.
        """
        system_parts: list[str] = []
        body_parts: list[str] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                body_parts.append(f"{m.role.upper()}: {m.content}")
        return "\n\n".join(system_parts), "\n\n".join(body_parts)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)

        try:
            from google import genai 
        except ImportError as exc:
            raise LLMProviderError(
                "Google GenAI package is not installed. "
                "Install it with: pip install google-genai"
            ) from exc

        model = self._model_for_request(request)
        client = self._get_client()
        system_text, body = self._split_system_and_body(request.messages)
        trace_prompt = self._prompt_for_trace(request)

        config: Dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        if system_text:
            config["system_instruction"] = system_text
        # `_extra_kwargs` filters Gemini's reserved keys as well as
        # OpenAI's, so a caller passing `max_tokens` in `extra` doesn't
        # silently override `max_output_tokens`.
        config.update(_extra_kwargs(request))

        self._trace_llm(model=model, prompt=trace_prompt)

        t0 = time.perf_counter()
        try:
            response = await self._call_with_retry(
                lambda: asyncio.to_thread(
                    client.models.generate_content,
                    model=_model_id(model),
                    contents=body,
                    config=cast("GenerateContentConfigOrDict", config),
                ),
                operation="models.generate_content",
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self._trace_llm(
                model=model, prompt=trace_prompt,
                error=str(exc), duration_ms=duration_ms,
                retries=self._retries_so_far,
            )
            raise LLMProviderError(
                f"Gemini request failed: {exc}"
            ) from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        content = response.text or ""

        # Gemini's usage_metadata carries prompt/candidate/total token
        # counts — without this, every Gemini call shows 0 tokens in the
        # trace, making cost comparison with OpenAI impossible.
        usage = None
        um = getattr(response, "usage_metadata", None)
        if um is not None:
            usage = LLMUsage(
                prompt_tokens=getattr(um, "prompt_token_count", 0) or 0,
                completion_tokens=(
                    getattr(um, "candidates_token_count", 0) or 0
                ),
                total_tokens=getattr(um, "total_token_count", 0) or 0,
            )

        finish_reason = getattr(response, "finish_reason", None)
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

        self._trace_llm(
            model=model, prompt=trace_prompt,
            response=content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=finish_reason,
            retries=self._retries_so_far,
        )

        return LLMResponse(
            content=content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
            metadata={},
        )


    @staticmethod
    def _gemini_finish_reason(reason: Any) -> Optional[str]:
        if reason is None:
            return None
        name = str(getattr(reason, "name", None) or reason).rsplit(".", 1)[-1].upper()
        return {"STOP": "stop", "MAX_TOKENS": "length"}.get(name, name.lower())

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self._validate_request(request)
        try:
            from google import genai  # noqa: F401
        except ImportError as exc:
            raise LLMProviderError(
                "Google GenAI package is not installed. Install it with: pip install google-genai"
            ) from exc

        model = self._model_for_request(request)
        client = self._get_client()
        system_text, body = self._split_system_and_body(request.messages)
        config: Dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        if system_text:
            config["system_instruction"] = system_text
        config.update(_extra_kwargs(request))

        async def _raw() -> AsyncIterator[_RawEvent]:
            sdk_stream = await client.aio.models.generate_content_stream(
                model=_model_id(model), contents=body, config=config,   # the SDK accepts a plain config mapping
            )
            try:
                async for ev in sdk_stream:
                    candidates = getattr(ev, "candidates", None) or []
                    text = (ev.text or "") if candidates else ""
                    finish = self._gemini_finish_reason(
                        getattr(candidates[0], "finish_reason", None)) if candidates else None
                    um = getattr(ev, "usage_metadata", None)
                    usage = None
                    if um is not None and getattr(um, "total_token_count", None):
                        usage = LLMUsage(
                            prompt_tokens=getattr(um, "prompt_token_count", 0) or 0,
                            completion_tokens=getattr(um, "candidates_token_count", 0) or 0,
                            total_tokens=getattr(um, "total_token_count", 0) or 0)
                    if text or finish or usage:
                        yield _RawEvent(text=text, finish_reason=finish, usage=usage)
            finally:
                await _aclose_quietly(sdk_stream)

        async for chunk in self._stream_events(
            model=model, prompt=self._prompt_for_trace(request), label="Gemini",
            open_raw=_raw, operation="models.generate_content_stream",
        ):
            yield chunk


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

def _sse_events(http: Any, endpoint: str, headers: Dict[str, str], payload: Dict[str, Any],
                label: str) -> AsyncIterator[_RawEvent]:
    """Open an OpenAI-compatible SSE completion and yield normalized events.

    Handles comment/keep-alive lines (`: ...`), blank lines, `[DONE]`, null deltas and
    in-band `{"error": ...}` payloads. The HTTP response is closed when this generator is
    closed (consumer cancelled) or exhausted.
    """
    async def _gen() -> AsyncIterator[_RawEvent]:
        async with http.stream("POST", endpoint, headers=headers, json=payload) as r:
            if r.status_code >= 400:
                body = _truncate_for_log((await r.aread()).decode("utf-8", "replace"))
                if r.status_code >= 500 or r.status_code == 429:
                    raise LLMProviderError(f"{label} HTTP {r.status_code}: {body}")
                raise LLMProviderError(
                    f"{label} HTTP {r.status_code} (invalid request or auth): {body}")
            async for line in r.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    err = _StreamError.model_validate_json(data)
                    chunk = _OAChunk.model_validate_json(data)
                except ValueError:
                    logger.debug("{} stream: ignoring unparsable data line", label)
                    continue
                if err.error:
                    raise LLMProviderError(f"{label} stream error: {err.error}")
                for out in _events_from_oa_chunk(chunk):
                    yield out
    return _gen()


class OpenRouterProvider(LLMProvider):
    name = LLMProviderName.OPENROUTER
    _endpoint = "https://openrouter.ai/api/v1/chat/completions"
    _label = "OpenRouter"      # shown in errors/logs; OpenAI-compatible subclasses override it

    def __init__(self, config: LLMProviderConfig):
        super().__init__(config)
        self._http = None

    @property
    def default_model(self) -> str:
        return "openai/gpt-4o-mini"

    def _get_http(self):
        """Lazy-init httpx client so the connection pool (and its TLS
        handshake) is reused across calls."""
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception as exc:
                logger.debug("{} client close failed: {}", self._label, exc)
            self._http = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)

        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise LLMProviderError(
                f"httpx is required for {self._label}."
            ) from exc

        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)
        http = self._get_http()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _model_id(model),
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        payload.update(_extra_kwargs(request))

        self._trace_llm(model=model, prompt=prompt)

        async def _call():
            r = await http.post(
                self._endpoint, headers=headers, json=payload,
            )
            # Capture the body — `raise_for_status()` produces messages
            # like "Client error '400 Bad Request'" with no explanation
            # of *which* parameter was wrong. The body carries it.
            if r.status_code >= 400:
                body = _truncate_for_log(r.text)
                if r.status_code >= 500 or r.status_code == 429:
                    # Transient — retry helper will handle it
                    raise LLMProviderError(
                        f"{self._label} HTTP {r.status_code}: {body}"
                    )
                # Permanent — raise with a distinguishing marker so
                # `_is_transient` doesn't retry a 400.
                raise LLMProviderError(
                    f"{self._label} HTTP {r.status_code} "
                    f"(invalid request or auth): {body}"
                )
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
                retries=self._retries_so_far,
            )
            raise LLMProviderError(
                f"{self._label} request failed: {exc}"
            ) from exc

        duration_ms = (time.perf_counter() - t0) * 1000
        data = response.json()
        choice = data["choices"][0]

        raw_content = choice["message"].get("content")
        finish_reason = choice.get("finish_reason")

        usage = None
        if data.get("usage"):
            u = data["usage"]
            usage = LLMUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
                provider_cost_usd=as_cost(u.get("total_cost", u.get("cost"))),
            )

        if raw_content is None:
            # Reasoning models consume the entire budget on hidden
            # reasoning and return `null` content when `max_tokens` is
            # hit. Surface clearly instead of silently sending "".
            logger.warning(
                "{} returned null content "
                "(model={}, finish_reason={}). Increase max_tokens "
                "or use a non-reasoning model.",
                self._label, model, finish_reason,
            )
            raw_content = ""

        self._trace_llm(
            model=model, prompt=prompt,
            response=raw_content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=finish_reason,
            retries=self._retries_so_far,
        )

        # Downgraded from INFO to DEBUG: full reply bodies at INFO
        # bloat `app.log` and leak whatever the user asked about.
        # `_trace_llm` above already records the content in the trace
        # store, where it's expected and queryable.
        logger.debug(
            "{} response: model={} finish_reason={} length={}",
            self._label, model, finish_reason, len(raw_content),
        )

        return LLMResponse(
            content=raw_content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
            metadata={"id": data.get("id")},
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self._validate_request(request)
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise LLMProviderError(f"httpx is required for {self._label}.") from exc

        model = self._model_for_request(request)
        http = self._get_http()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload: Dict[str, Any] = {
            "model": _model_id(model),
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        payload.update(_extra_kwargs(request))

        async for chunk in self._stream_events(
            model=model, prompt=self._prompt_for_trace(request), label=self._label,
            open_raw=lambda: _sse_events(http, self._endpoint, headers, payload, self._label),
            operation="chat.completions.stream",
        ):
            yield chunk


class AIRouterProvider(OpenRouterProvider):
    """AIRouter (https://www.airouter.in): an OpenAI-compatible gateway, so it reuses the OpenRouter
    HTTP/SSE implementation (same request/response/stream shapes, Bearer auth) with its own endpoint.

    Models are `<provider>/<model>` slugs from GET https://api.airouter.in/v1/models (e.g.
    `google/gemini-3-flash`); the catalog changes, so none are hardcoded. `llm_provider.base_url`
    overrides the API base (default https://api.airouter.in/v1).
    """
    name = LLMProviderName.AIROUTER
    _label = "AIRouter"
    DEFAULT_BASE_URL = "https://api.airouter.in/v1"

    def __init__(self, config: LLMProviderConfig):
        super().__init__(config)
        base = (getattr(config, "base_url", None) or self.DEFAULT_BASE_URL).rstrip("/")
        self._endpoint = f"{base}/chat/completions"

    @property
    def default_model(self) -> str:
        return "google/gemini-3-flash"


# ---------------------------------------------------------------------------
# Kimi / Moonshot
# ---------------------------------------------------------------------------

class KimiProvider(LLMProvider):
    name = LLMProviderName.KIMI
    _endpoint = "https://api.moonshot.ai/v1/chat/completions"

    def __init__(self, config: LLMProviderConfig):
        super().__init__(config)
        self._http = None

    @property
    def default_model(self) -> str:
        return "moonshot-v1-8k"

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception as exc:
                logger.debug("Kimi client close failed: {}", exc)
            self._http = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_request(request)

        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise LLMProviderError(
                "httpx is required for Kimi."
            ) from exc

        model = self._model_for_request(request)
        prompt = self._prompt_for_trace(request)
        http = self._get_http()

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
        payload.update(_extra_kwargs(request))

        self._trace_llm(model=model, prompt=prompt)

        async def _call():
            r = await http.post(
                self._endpoint, headers=headers, json=payload,
            )
            if r.status_code >= 400:
                body = _truncate_for_log(r.text)
                if r.status_code >= 500 or r.status_code == 429:
                    raise LLMProviderError(
                        f"Kimi HTTP {r.status_code}: {body}"
                    )
                raise LLMProviderError(
                    f"Kimi HTTP {r.status_code} "
                    f"(invalid request or auth): {body}"
                )
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
                retries=self._retries_so_far,
            )
            raise LLMProviderError(
                f"Kimi request failed: {exc}"
            ) from exc

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

        raw_content = choice["message"].get("content")
        finish_reason = choice.get("finish_reason")
        if raw_content is None:
            logger.warning(
                "Kimi returned null content "
                "(model={}, finish_reason={}). Increase max_tokens.",
                model, finish_reason,
            )
            raw_content = ""

        self._trace_llm(
            model=model, prompt=prompt,
            response=raw_content, usage=usage,
            duration_ms=duration_ms,
            finish_reason=finish_reason,
            retries=self._retries_so_far,
        )

        logger.debug(
            "Kimi response: model={} finish_reason={} length={} "
            "usage={} request_id={}",
            model, finish_reason, len(raw_content),
            usage, data.get("id"),
        )

        return LLMResponse(
            content=raw_content,
            provider=self.name,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
            metadata={"id": data.get("id")},
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self._validate_request(request)
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise LLMProviderError("httpx is required for Kimi.") from exc

        model = self._model_for_request(request)
        http = self._get_http()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload: Dict[str, Any] = {
            "model": _model_id(model),
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        payload.update(_extra_kwargs(request))

        async for chunk in self._stream_events(
            model=model, prompt=self._prompt_for_trace(request), label="Kimi",
            open_raw=lambda: _sse_events(http, self._endpoint, headers, payload, "Kimi"),
            operation="chat.completions.stream",
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

LLM_PROVIDER_CLASSES: Dict[LLMProviderName, Type[LLMProvider]] = {
    LLMProviderName.OPENAI: OpenAIProvider,
    LLMProviderName.GEMINI: GeminiProvider,
    LLMProviderName.OPENROUTER: OpenRouterProvider,
    LLMProviderName.AIROUTER: AIRouterProvider,
    LLMProviderName.KIMI: KimiProvider,
}


def create_provider(config: LLMProviderConfig) -> LLMProvider:
    """Create an LLM provider from config.

    Raises `LLMConfigurationError` (with the list of valid names) on an
    unknown provider rather than a bare `KeyError` with no context.
    """
    cls = LLM_PROVIDER_CLASSES.get(config.provider)
    if cls is None:
        raise LLMConfigurationError(
            f"Unknown LLM provider {config.provider!r}. "
            f"Available: {sorted(name.value for name in LLM_PROVIDER_CLASSES)}"
        )
    return cls(config)