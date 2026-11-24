from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


from pai.common.enums.llm import LLMModel, LLMProviderName


@dataclass
class LLMMessage:
    """
    A single message in an LLM conversation.

    role:
        system | user | assistant | tool
    """

    role: str
    content: str

    def __post_init__(self) -> None:
        allowed_roles = {"system", "user", "assistant", "tool"}

        if self.role not in allowed_roles:
            raise ValueError(
                f"Invalid message role '{self.role}'. "
                f"Expected one of {sorted(allowed_roles)}."
            )

        if not isinstance(self.content, str):
            raise TypeError("Message content must be a string.")


@dataclass
class LLMRequest:
    """
    Provider-independent LLM request.
    """

    messages: List[LLMMessage]

    model: Optional[LLMModel] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = 1024

    provider: Optional[LLMProviderName] = None

    # Estimated tokens per named part of the prompt (see pai.llm.breakdown); None = derive from message roles.
    breakdown: Optional[Dict[str, int]] = None

    # Optional provider-specific parameters.
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("LLMRequest requires at least one message.")

        if not 0.0 <= self.temperature:
            raise ValueError("temperature must be >= 0.")

        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens must be greater than 0.")


@dataclass
class LLMUsage:
    """
    Normalized token usage information.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Cost (USD). `provider_cost_usd` is what the gateway itself reported (AIRouter/OpenRouter);
    # `cost_usd` is the final figure used for accounting and `cost_source` says where it came from
    # ("configured" = tokens x configured price per million, "provider", or None = unknown).
    provider_cost_usd: Optional[float] = None
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    breakdown: Optional[Dict[str, int]] = None   # prompt tokens per part (system_prompt, memory, history...); sums to prompt_tokens
    recorded: bool = False        # set once the call has been added to the cost tracker (never twice)

    def __post_init__(self) -> None:
        self.prompt_tokens = max(0, self.prompt_tokens)
        self.completion_tokens = max(0, self.completion_tokens)

        if self.total_tokens <= 0:
            self.total_tokens = (
                self.prompt_tokens + self.completion_tokens
            )


@dataclass
class LLMStreamChunk:
    """One incremental piece of a streamed completion.

    ``delta`` is new text only (never the accumulated text). The last chunk carries
    ``finish_reason`` (and ``usage`` when the provider reports it).
    """

    delta: str = ""
    finish_reason: Optional[str] = None
    usage: Optional["LLMUsage"] = None


@dataclass
class LLMResponse:
    """
    Provider-independent LLM response.
    """

    content: str
    provider: LLMProviderName
    model: LLMModel

    usage: Optional[LLMUsage] = None

    finish_reason: Optional[str] = None

    # Provider-specific metadata.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("LLMResponse.content must be a string.")

