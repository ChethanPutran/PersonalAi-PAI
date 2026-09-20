from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .models import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
)
from .provider import (
    LLMProvider,
    create_provider,
)


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
        default_provider: str = "openai",
        default_model: Optional[str] = None,
    ):
        self.default_route = ModelRoute(
            provider=default_provider,
            model=default_model,
        )

        self._providers: Dict[str, LLMProvider] = {}
        self._routes: Dict[str, ModelRoute] = {}

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def register_provider(
        self,
        name: str,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
        base_url: Optional[str] = None,
        **extra,
    ) -> LLMProvider:

        provider = create_provider(
            name,
            api_key=api_key,
            model=model,
            timeout=timeout,
            base_url=base_url,
            **extra,
        )

        self._providers[name] = provider

        return provider

    def get_provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            raise ValueError(
                f"Provider '{name}' is not registered."
            )

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
            raise ValueError(
                f"Provider '{provider}' is not registered."
            )

        self._routes[route_name] = ModelRoute(
            provider=provider,
            model=model,
        )

    def resolve_route(
        self,
        route: Optional[str] = None,
    ) -> ModelRoute:

        if route is None:
            return self.default_route

        if route not in self._routes:
            raise ValueError(
                f"Unknown LLM route '{route}'."
            )

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

        selected_route = self.resolve_route(route)

        provider = self.get_provider(
            selected_route.provider
        )

        selected_model = (
            model
            or selected_route.model
        )

        request = LLMRequest(
            messages=messages,
            model=selected_model,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=selected_route.provider,
            extra=extra,
        )

        return await provider.generate(request)

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

        messages = []

        if system_prompt:
            messages.append(
                LLMMessage(
                    role="system",
                    content=system_prompt,
                )
            )

        messages.append(
            LLMMessage(
                role="user",
                content=prompt,
            )
        )

        response = await self.generate(
            messages,
            route=route,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )

        return response.content

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(
        self,
        provider: Optional[str] = None,
    ) -> Dict[str, bool]:

        if provider:
            return {
                provider: await self.get_provider(
                    provider
                ).health_check()
            }

        results = {}

        for name, instance in self._providers.items():
            results[name] = await instance.health_check()

        return results