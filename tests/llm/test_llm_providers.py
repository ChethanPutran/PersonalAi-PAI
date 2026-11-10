#!/usr/bin/env python3
"""
Smoke-test OpenRouter and Kimi (Moonshot) through the pai LLM provider layer.

Uses `pai.llm.providers.create_provider` and `LLMProvider.generate()` so the
test exercises the same code path the orchestrator uses — including the
transient-failure retry wrapper, tracing, and provider-specific error
wrapping.

Usage:
    export OPENROUTER_API_KEY=sk-or-v1-...
    export KIMI_API_KEY=sk-...
    python scripts/test_llm_providers.py

Optional overrides:
    OPENROUTER_TEST_MODEL   (default: deepseek/deepseek-r1-0528:free)
    KIMI_TEST_MODEL         (default: moonshot-v1-8k)

Note: `generate()` retries transient failures (429 rate limits included),
so a rate-limited request will take ~7s longer before reporting failure.
Daily-quota 429s contain "quota exceeded", which the retry classifier
treats as permanent — those fail immediately.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Optional

from pai.llm.models import (
    LLMMessage,
    LLMProviderConfig,
    LLMRequest,
    LLMResponse,
)
from pai.llm.provider import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    create_provider,
)


PROMPT = "Reply with exactly the words: provider test ok"

OPENROUTER_MODEL = os.getenv(
    "OPEMROUTER_MODEL_NAME", "deepseek/deepseek-r1-0528:free",
)
KIMI_MODEL = os.getenv("KIMI_MODEL_NAME", "moonshot-v1-8k")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    print(f"     {msg}")


# ---------------------------------------------------------------------------
# Core runner — identical for every provider
# ---------------------------------------------------------------------------

async def _run(provider: LLMProvider, label: str) -> bool:
    _banner(f"{label}  ·  provider={provider.name}  ·  model={provider.model}")

    request = LLMRequest(
        messages=[LLMMessage(role="user", content=PROMPT)],
        max_tokens=1024,
        temperature=0.0,
    )

    t0 = time.perf_counter()
    try:
        response: LLMResponse = await provider.generate(request)
    except LLMConfigurationError as exc:
        _fail(f"config error: {exc}")
        return False
    except LLMProviderError as exc:
        _fail(f"provider error: {exc}")
        return False
    except Exception as exc:                       # noqa: BLE001
        _fail(f"unexpected {type(exc).__name__}: {exc}")
        return False

    elapsed_ms = (time.perf_counter() - t0) * 1000

    _ok(f"reply       : {response.content!r}")
    _ok(f"model used  : {response.model}")
    _ok(f"served by   : {response.provider}")
    if response.usage is not None:
        _ok(
            "tokens      : "
            f"{response.usage.total_tokens} "
            f"(prompt={response.usage.prompt_tokens}, "
            f"completion={response.usage.completion_tokens})"
        )
    if response.finish_reason:
        _ok(f"finish      : {response.finish_reason}")
    if response.metadata and "id" in response.metadata:
        _ok(f"request id  : {response.metadata['id']}")
    _ok(f"duration    : {elapsed_ms:.0f}ms")
    return True


# ---------------------------------------------------------------------------
# Per-provider wrappers
# ---------------------------------------------------------------------------

async def test_openrouter() -> bool:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        _banner("OpenRouter  ·  skipped")
        _info("OPENROUTER_API_KEY not set")
        return False

    config = LLMProviderConfig(
        provider="openrouter",
        model_name=OPENROUTER_MODEL,
        api_key=key,
        temperature=0.0,
        max_tokens=1024,
    )
    provider = create_provider(config)
    return await _run(provider, "OpenRouter")



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    print("pai LLM provider smoke test")
    print(f"prompt: {PROMPT!r}")

    results = {
        "openrouter": await test_openrouter()
    }

    _banner("Summary")
    for name, ok in results.items():
        (_ok if ok else _fail)(name)

    # Treat "skipped" (no key) as neither pass nor fail, but signal
    # clearly in the exit code so CI catches accidental regressions.
    if any(results.values()):
        return 0 if all(results.values()) else 1
    return 2  # nothing was tested — no keys present


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))