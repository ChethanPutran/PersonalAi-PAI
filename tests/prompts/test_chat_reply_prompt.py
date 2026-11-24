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
        modality="text", intent="reply", clarify_question="", clarify_options="",
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
        tone="brief", is_retry=False, modality="text", intent="reply", clarify_question="", clarify_options="",
    )
    assert "## TONE" in out
    assert "Be concise" in out


def test_retry_hint_only_when_flagged():
    registry = _registry()
    base = dict(
        goal="x", capability_block="", memory_block="M",
        history_block="", prior_results_block="", tone="friendly", modality="text", intent="reply", clarify_question="", clarify_options="",
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
        tone="friendly", is_retry=False, modality="text", intent="reply", clarify_question="", clarify_options="",
    )
    assert "<<<BEGIN_USER_GOAL>>>" in out
    assert "take a photo" in out
    assert "TOOL RESULTS" in out
    assert "Summarize what was done for the user" in out


def _render(modality: str, intent: str = "reply", question: str = "", options: str = "") -> str:
    return _registry().get("chat.reply").render(
        goal="hi", capability_block="", memory_block="", history_block="",
        prior_results_block="", tone="friendly", is_retry=False, modality=modality,
        intent=intent, clarify_question=question, clarify_options=options,
    )


def test_voice_modality_adds_spoken_output_rules():
    out = _render("voice")
    assert "## VOICE OUTPUT" in out and "SPOKEN aloud" in out
    assert "No markdown" in out and "confirm it on their screen" in out


def test_text_modality_has_no_voice_section():
    assert "VOICE OUTPUT" not in _render("text")



def test_clarify_section_only_for_clarify_intent():
    assert "## CLARIFICATION" not in _render("text")
    out = _render("text", "clarify", "Which camera?", "front, back")
    assert "## CLARIFICATION" in out and "<<<BEGIN_CLARIFICATION>>>" in out
    assert "Which camera?" in out and "front, back" in out
