from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional

from loguru import logger

from pai.common.enums.llm import LLMModel
from pai.config.models.llm import LLMProviderConfig, ModelPrice
from pai.observability.tracer import get_tracer

from .models import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMStreamChunk,
)
from .breakdown import current_breakdown, role_breakdown
from .cost import CostTracker, PriceBook, current_route
from .provider import LLMProvider, create_provider


# Routes the system itself asks for. Unregistered, they fall back to the default provider/model, so a
# deployment that configures none behaves exactly as before.
ROUTE_PLANNER = "planner"   # goal decomposition (strict JSON DAG)
ROUTE_CHAT = "chat"         # conversational replies and voice (latency-sensitive)
ROUTE_AGENT = "agent"       # long-running ReAct agents (JSON actions)
WELL_KNOWN_ROUTES = frozenset({ROUTE_PLANNER, ROUTE_CHAT, ROUTE_AGENT})


def _as_model(model):
    """Route/caller models arrive as plain strings (config); providers expect LLMModel."""
    if model is None or isinstance(model, LLMModel):
        return model
    return LLMModel(model) if str(model).strip() else None


@contextlib.contextmanager
def _route_scope(route: Optional[str], request: Optional[LLMRequest] = None):
    """Tag every LLM call made inside with its route (usage per role) and with the request's prompt
    breakdown (usage per prompt part). A request without an explicit breakdown is split by message role."""
    token = current_route.set(route or "default")
    parts = None
    if request is not None:
        parts = request.breakdown or role_breakdown(request.messages)
    btoken = current_breakdown.set(parts)
    try:
        yield
    finally:
        for var, tok in ((current_route, token), (current_breakdown, btoken)):
            try:
                var.reset(tok)
            except ValueError:      # reset from a different context (async generator resumed elsewhere)
                var.set(None)


def _usage_attrs(span, response) -> None:
    """Tokens and cost on the router span, so a trace shows what each request used and cost."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    span.set_attribute("prompt_tokens", u.prompt_tokens)
    span.set_attribute("completion_tokens", u.completion_tokens)
    span.set_attribute("total_tokens", u.total_tokens)
    if u.cost_usd is not None:
        span.set_attribute("cost_usd", round(u.cost_usd, 8))
        span.set_attribute("cost_source", u.cost_source)


@dataclass
class ModelRoute:
    """
        Defines which provider/model should handle a request.
        """
    provider: str
    model: Optional[str] = None


class LLMRouter:
    """
        Central LLM routing layer for PAI.
    
        Responsibilities:
            - Manage configured providers
            - Select providers/models
            - Provide a common generation interface
            - Allow different agents to request different models
        """
    def __init__(
        self,
        default_config: LLMProviderConfig,
        *,
        pricing: Optional[Dict[str, ModelPrice]] = None,
        cost_tracker: Optional[CostTracker] = None,
    ):
        # Token/cost accounting shared by every provider this router creates (see pai.llm.cost).
        self.pricing: Dict[str, ModelPrice] = dict(pricing or {})
        self.cost_tracker = cost_tracker
        default_provider = (
            default_config.provider if default_config else "openai"
        )
        default_model = (
            default_config.model_name
            if default_config and default_config.model_name
            else "gpt-3.5-turbo"
        )

        logger.info(
            "Initializing LLMRouter with default provider '{}' and model '{}'",
            default_provider, default_model,
        )

        self.default_route = ModelRoute(
            provider=default_provider,
            model=default_model,
        )

        if default_config is None:
            default_config = LLMProviderConfig(
                name=default_provider,
                model=default_model,
            )

        self._providers: Dict[str, LLMProvider] = {
            self.default_route.provider: self._create(default_config)
        }
        self._routes: Dict[str, ModelRoute] = {}

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register_provider(self, config) -> LLMProvider:
        tracer = get_tracer()
        with tracer.span(
            "router.register_provider",
            provider=config.provider,
            model=config.model_name,
        ):
            llm = self._create(config)
            self._providers[config.provider] = llm
            return llm

    def _create(self, config: LLMProviderConfig) -> LLMProvider:
        """Build a provider and hand it the price book and the shared cost tracker."""
        provider = create_provider(config)
        default = None
        cin, cout = config.cost_per_million_input, config.cost_per_million_output
        if cin is not None or cout is not None:
            default = ModelPrice(input_per_million=cin or 0.0, output_per_million=cout or 0.0)
        provider.price_book = PriceBook(self.pricing, default)
        provider.cost_tracker = self.cost_tracker
        return provider

    def reconfigure(self, default_config: LLMProviderConfig, *, routes: Optional[Dict[str, str]] = None,
                    pricing: Optional[Dict[str, ModelPrice]] = None, close_after_seconds: float = 60.0) -> None:
        """Apply a new provider/model/routes/prices to the RUNNING router (settings reload).

        Everything that can fail (building the provider needs its API key) happens before anything is swapped, so a bad
        configuration leaves the router exactly as it was. Requests already running keep the old provider; its HTTP
        client is closed after a grace period."""
        new_pricing = dict(pricing if pricing is not None else self.pricing)
        self.pricing = new_pricing
        provider = self._create(default_config)                     # may raise: nothing has been changed yet
        old = list(self._providers.values())
        default_model = default_config.model_name if default_config.model_name else self.default_route.model
        self._providers = {default_config.provider: provider}
        self.default_route = ModelRoute(provider=default_config.provider, model=default_model)
        self._routes = {}
        for role, model in (routes or {}).items():
            self.register_route(role, default_config.provider, model)
        logger.info("LLMRouter reconfigured: provider '{}' model '{}', routes {}", default_config.provider, default_model,
                    sorted(self._routes))
        if old:
            try:
                loop = asyncio.get_running_loop()
                for p in old:
                    loop.call_later(close_after_seconds, lambda p=p: asyncio.ensure_future(p.aclose()))
            except RuntimeError:                                    # no loop (a test): nothing to schedule
                pass

    def get_provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' is not registered.")
        return self._providers[name]

    def remove_provider(self, name: str) -> None:
        self._providers.pop(name, None)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def register_route(
        self,
        route_name: str,
        provider: str,
        model: Optional[str] = None,
    ) -> None:
        if provider not in self._providers:
            raise ValueError(f"Provider '{provider}' is not registered.")
        self._routes[route_name] = ModelRoute(provider=provider, model=_as_model(model))

    def resolve_route(self, route: Optional[str] = None) -> ModelRoute:
        if route is None:
            return self.default_route
        if route not in self._routes:
            if route in WELL_KNOWN_ROUTES:
                return self.default_route
            raise ValueError(f"Unknown LLM route '{route}'.")
        return self._routes[route]

    async def aclose(self) -> None:
        """Close every provider's HTTP/SDK client."""
        for name, provider in list(self._providers.items()):
            try:
                await provider.aclose()
            except Exception as exc:
                logger.warning("LLM provider {} did not close cleanly: {}", name, exc)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def stream_request(
        self,
        request: LLMRequest,
        *,
        route: Optional[str] = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a pre-built LLMRequest (same routing rules as ``generate_request``)."""
        selected_route = self.resolve_route(route)
        provider = self.get_provider(selected_route.provider)
        if not request.model:
            request.model = selected_route.model
        # aclosing: if the consumer stops early (barge-in, client gone) the upstream
        # connection is closed immediately instead of waiting for garbage collection.
        async with contextlib.aclosing(provider.stream(request)) as chunks:
            while True:
                # The route tag is set only while the provider's code runs (each step), never held
                # across a yield into the consumer's context.
                with _route_scope(route, request):
                    try:
                        chunk = await chunks.__anext__()
                    except StopAsyncIteration:
                        break
                yield chunk

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    async def generate_request(
        self,
        request: LLMRequest,
        *,
        route: Optional[str] = None,
    ) -> LLMResponse:
        """Generate from a pre-built LLMRequest.

        Preserves the caller's `model`, `temperature`, `max_tokens`, and
        `extra` fields verbatim — unlike `generate()`, which overrides them
        with its own keyword arguments. Use this when the caller has already
        assembled an LLMRequest and doesn't want the router's defaults
        applied on top.
        """
        tracer = get_tracer()
        selected_route = self.resolve_route(route)
        provider = self.get_provider(selected_route.provider)
        # Only override the model if the request didn't specify one, so a
        # route-level default doesn't clobber an explicit per-request choice.
        if not request.model:
            request.model = selected_route.model

        async with tracer.span(
            "router.generate_request",
            route=route or "default",
            provider=selected_route.provider,
            model=request.model,
            message_count=len(request.messages),
        ) as span:
            t0 = time.perf_counter()
            try:
                with _route_scope(route, request):
                    response = await provider.generate(request)
                span.set_attribute(
                    "duration_ms",
                    round((time.perf_counter() - t0) * 1000, 1),
                )
                span.set_attribute("response_length", len(response.content))
                _usage_attrs(span, response)
                return response
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        route: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = 1000,
        **extra,
    ) -> LLMResponse:
        tracer = get_tracer()

        selected_route = self.resolve_route(route)
        provider = self.get_provider(selected_route.provider)
        selected_model = _as_model(model) or selected_route.model

        async with tracer.span(
            "router.generate",
            route=route or "default",
            provider=selected_route.provider,
            model=selected_model,
            message_count=len(messages),
        ) as span:
            # Optional: set message_count after entering the span so a type
            # bug in the caller produces a traceback pointing at *that* line
            # instead of at the span's kwarg evaluation.
            span.set_attribute("message_count", len(messages))

            span.set_attribute(
                "prompt_length",
                sum(len(m.content) for m in messages),
            )

            request = LLMRequest(
                messages=messages,
                model=selected_model,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=selected_route.provider,
                extra=extra,
            )

            t0 = time.perf_counter()
            try:
                with _route_scope(route, request):
                    response = await provider.generate(request)
                duration_ms = (time.perf_counter() - t0) * 1000
                span.set_attribute("duration_ms", round(duration_ms, 1))
                span.set_attribute("response_length", len(response.content))
                _usage_attrs(span, response)
                return response
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                raise

    async def complete(
        self,
        prompt: str,
        *,
        route: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = 1000,
        **extra,
    ) -> str:
        tracer = get_tracer()
        async with tracer.span(
            "router.complete",
            route=route or "default",
            prompt_length=len(prompt),
        ) as span:

            messages = []
            if system_prompt:
                messages.append(
                    LLMMessage(role="system", content=system_prompt)
                )
            messages.append(LLMMessage(role="user", content=prompt))

            response = await self.generate(
                messages,
                route=route,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                **extra,
            )
            span.set_attribute("response_length", len(response.content))
            return response.content

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(
        self,
        provider: Optional[str] = None,
    ) -> Dict[str, bool]:
        tracer = get_tracer()
        async with tracer.span(
            "router.health_check",
            provider=provider or "all",
        ) as span:
            if provider:
                result = {
                    provider: await self.get_provider(provider).health_check()
                }
            else:
                result = {}
                for name, instance in self._providers.items():
                    result[name] = await instance.health_check()
            span.set_attribute("results", result)
            return result