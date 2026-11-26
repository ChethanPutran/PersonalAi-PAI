from pai.security.guards.llm_guard import TOOL_CALL_PATTERNS


def _strip(text):
    for pattern, _ in TOOL_CALL_PATTERNS:
        text = pattern.sub("", text)
    return text


def test_tag_tool_call_is_stripped():
    out = _strip("Let me check.\n<tool_call>location.get\n\n</tool_call>")
    assert "tool_call" not in out and "location.get" not in out
    assert out.startswith("Let me check.")


def test_unclosed_tag_is_stripped_to_end():
    assert "location" not in _strip("Hi <tool_call>location.get")


def test_streaming_never_releases_tag_call():
    from pai.security.guards.llm_guard import PromptGuard
    from pai.security.guards.stream_guard import StreamingSanitizer
    from pai.config.models.prompts import PromptsGuardConfig
    s = StreamingSanitizer(PromptGuard(PromptsGuardConfig()))
    out = ""
    for chunk in ["Sure.\n<tool_", "call>location.get\n", "\n</tool_call>", " Done."]:
        out += s.feed(chunk)
    out += s.flush()
    assert "tool_call" not in out and "location.get" not in out
