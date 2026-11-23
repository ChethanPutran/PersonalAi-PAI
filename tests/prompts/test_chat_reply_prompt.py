# tests/prompts/test_chat_reply_prompt.py
from pathlib import Path
from pai.prompts.registry import PromptRegistry
from pai.config.models.prompts import PromptRegistryConfig


def _registry() -> PromptRegistry:
    return PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True))


def test_friendly_no_context():
    """Simplest case: chat-only, no memory, no history, no results."""
    registry = _registry()
    out = registry.get("chat.reply").render(
        goal="Hi",
        capability_block="",
        memory_block="",
        history_block="",
        prior_results_block="",
        tone="friendly",
        is_retry=False,
    )
    # Base instructions
    assert "warm, friendly personal AI assistant" in out
    assert "Do NOT emit JSON, tool-call envelopes" in out
    # No spurious sections
    assert "## TONE" not in out
    assert "LIVE CAPABILITIES" not in out
    assert "RECENT CONVERSATION" not in out
    assert "## TOOL RESULTS" not in out
    # The bare goal appears in the user turn markers
    assert "<<<BEGIN_USER_GOAL>>>" in out
    assert "Hi" in out


def test_tone_brief_renders():
    registry = _registry()
    out = registry.get("chat.reply").render(
        goal="Hi", capability_block="", memory_block="",
        history_block="", prior_results_block="",
        tone="brief", is_retry=False,
    )
    assert "## TONE" in out
    assert "Be concise" in out


def test_retry_hint_only_when_flagged():
    registry = _registry()
    base = dict(
        goal="x", capability_block="", memory_block="M",
        history_block="", prior_results_block="", tone="friendly",
    )
    assert "RETRY" not in registry.get("chat.reply").render(
        is_retry=False, **base,
    )
    assert "This is a RETRY" in registry.get("chat.reply").render(
        is_retry=True, **base,
    )


def test_prior_results_changes_user_turn():
    registry = _registry()
    out = registry.get("chat.reply").render(
        goal="take a photo",
        capability_block="", memory_block="", history_block="",
        prior_results_block='{"success": true, "result": {...}}',
        tone="friendly", is_retry=False,
    )
    assert "<<<BEGIN_USER_GOAL>>>" in out
    assert "take a photo" in out
    assert "TOOL RESULTS" in out
    assert "Summarize what was done for the user" in out
