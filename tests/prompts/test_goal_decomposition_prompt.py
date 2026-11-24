# tests/prompts/test_goal_decomposition_prompt.py
from pathlib import Path

import pytest
from pai.prompts.registry import PromptRegistry
from pai.config.models.prompts import PromptRegistryConfig


def _registry() -> PromptRegistry:
    return PromptRegistry(PromptRegistryConfig(root="prompts", frozen=True))


def test_current_version_renders():
    registry = _registry()
    t = registry.get("planning.goal_decomposition")
    out = t.render(
        goal="remind me at 3pm",
        capabilities_block="- schedule.create\n    …",
        memory_block="",
        history_block="",
        is_retry=False,
        user_tz="Asia/Kolkata",
        current_time="2026-11-14T10:00:00+00:00",
        chat_capability_id="chat.reply",
        source_device="dev-1",
    )
    assert "remind me at 3pm" in out
    assert "Asia/Kolkata" in out
    assert "RETRYING" not in out         # is_retry=False


def test_retry_rule_appears_only_when_is_retry():
    registry = _registry()
    t = registry.get("planning.goal_decomposition")
    base = dict(
        goal="x", capabilities_block="", memory_block="",
        history_block="", user_tz="UTC",
        current_time="…", chat_capability_id="chat.reply", source_device="",
    )
    assert "RETRYING" not in t.render(is_retry=False, **base)
    assert "RETRYING" in t.render(is_retry=True, **base)


def test_missing_variable_raises_clearly():
    registry = _registry()
    t = registry.get("planning.goal_decomposition")
    with pytest.raises(ValueError, match="missing variable"):
        t.render(goal="x")   # deliberately missing everything else


def _render_current(**over):
    base = dict(
        goal="take a photo", capabilities_block="- camera.capture", memory_block="",
        history_block="", is_retry=False, user_tz="UTC", current_time="2026-11-24T00:00:00+00:00",
        chat_capability_id="chat.reply", source_device="",
    )
    base.update(over)
    return _registry().get("planning.goal_decomposition").render(**base)


def test_prompt_does_not_ask_llm_for_type_and_has_ambiguity_rule():
    out = _render_current()
    assert '"type"' not in out.replace('Do NOT output a "type" field', "")
    assert "AMBIGUITY" in out and '"intent":"clarify"' in out
    assert "ADDITIONAL CONTEXT" not in out


def test_untrusted_blocks_are_spotlighted():
    out = _render_current(history_block="user: hi", memory_block="m")
    for tag in ("CONVERSATION_HISTORY", "PAST_INTERACTIONS", "USER_GOAL"):
        assert f"<<<BEGIN_{tag}>>>" in out and f"<<<END_{tag}>>>" in out
    assert "CONTENT SAFETY" in out
