# tests/prompts/test_chat_reply_prompt.py
from pathlib import Path
from pai.prompts.registry import PromptRegistry


def test_friendly_no_context():
    """Simplest case: chat-only, no memory, no history, no results."""
    registry = PromptRegistry(Path("prompts"), frozen=True)
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
    assert 'Do NOT emit tool-call syntax' in out
    # No spurious sections
    assert "TONE:" not in out
    assert "LIVE CAPABILITIES" not in out
    assert "RECENT CONVERSATION" not in out
    assert "Results from the tools" not in out
    # The bare goal appears once as the user turn
    assert out.rstrip().endswith("Hi")


def test_tone_brief_renders():
    registry = PromptRegistry(Path("prompts"), frozen=True)
    out = registry.get("chat.reply").render(
        goal="Hi", capability_block="", memory_block="",
        history_block="", prior_results_block="",
        tone="brief", is_retry=False,
    )
    assert "TONE: Be concise" in out


def test_retry_hint_only_when_flagged():
    registry = PromptRegistry(Path("prompts"), frozen=True)
    base = dict(
        goal="x", capability_block="", memory_block="M",
        history_block="", prior_results_block="", tone="friendly",
    )
    assert "This is a RETRY" not in registry.get("chat.reply").render(
        is_retry=False, **base,
    )
    assert "This is a RETRY" in registry.get("chat.reply").render(
        is_retry=True, **base,
    )


def test_prior_results_changes_user_turn():
    registry = PromptRegistry(Path("prompts"), frozen=True)
    out = registry.get("chat.reply").render(
        goal="take a photo",
        capability_block="", memory_block="", history_block="",
        prior_results_block='{"success": true, "result": {...}}',
        tone="friendly", is_retry=False,
    )
    assert "User message: take a photo" in out
    assert "Results from the tools that were executed:" in out
    assert "Now reply to the user in natural language" in out