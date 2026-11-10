from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger

from pai.observability.tracer import get_tracer

from .models import (
    LLMMessage,
    LLMProviderConfig,
    LLMRequest,
    LLMResponse,
)
from .provider import LLMProvider, create_provider


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
    def __init__(self, default_config: LLMProviderConfig):
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
            self.default_route.provider: create_provider(default_config)
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
            llm = create_provider(config)
            self._providers[config.provider] = llm
            return llm

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
        self._routes[route_name] = ModelRoute(provider=provider, model=model)

    def resolve_route(self, route: Optional[str] = None) -> ModelRoute:
        if route is None:
            return self.default_route
        if route not in self._routes:
            raise ValueError(f"Unknown LLM route '{route}'.")
        return self._routes[route]

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

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
        selected_model = model or selected_route.model

        async with tracer.span(
            "router.generate",
            route=route or "default",
            provider=selected_route.provider,
            model=selected_model,
            message_count=len(messages),
        ) as span:

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
                response = await provider.generate(request)
                duration_ms = (time.perf_counter() - t0) * 1000
                span.set_attribute("duration_ms", round(duration_ms, 1))
                span.set_attribute("response_length", len(response.content))
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