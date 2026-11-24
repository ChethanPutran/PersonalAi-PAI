"""Incremental output sanitizer for streamed replies (text chat and voice).

`PromptGuard.sanitize_response` needs the whole reply (it trims whitespace and may
substitute a fallback), so it cannot be applied to a token stream. This does the same
job incrementally, with the same pattern sets and the same switches:

  * text is released at WORD boundaries (so secrets, which are whitespace-free tokens,
    are always seen whole, and the stream still feels live);
  * a line that might still turn into a tool-call envelope (`{"tool": …`, `name.fn(…)`,
    a fenced json block) or a PEM key is HELD until it can be judged;
  * everything that is released has had tool envelopes stripped and secrets redacted.

A secret or tool envelope can therefore never reach the screen or the speaker, even
though the model produced it token by token.
"""
from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel

from pai.security.guards.llm_guard import (
    JAILBREAK_SUCCESS_PATTERNS,
    SECRET_PATTERNS,
    TOOL_CALL_PATTERNS,
    PromptGuard,
)

# A partial line that could still become a tool envelope.
_ENVELOPE_START = re.compile(r"^\s*(?:\{|```|[A-Za-z_]\w*\.[A-Za-z_][\w.]*\()")
# A single bare first token ("camera.capture") that a "(" might still follow.
_BARE_TOKEN = re.compile(r"^\s*[A-Za-z_][\w.]*$")
_TRAILING_BEARER = re.compile(r"\bBearer\s*$", re.I)
_OPEN_FENCE = re.compile(r"^\s*```(?:json)?\s*$", re.I)
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_MAX_HELD_CHARS = 8000          # never hold more than this (an unterminated block is flushed)


class SanitizeStats(BaseModel):
    tool_calls_stripped: int = 0
    secrets_redacted: int = 0
    jailbreak_markers: int = 0


class StreamingSanitizer:
    def __init__(self, guard: PromptGuard) -> None:
        mode = guard.response_mode.value if hasattr(guard.response_mode, "value") else guard.response_mode
        self._off = mode == "off"
        self._strip_tools = guard.strip_tool_calls
        self._redact = guard.redact_secrets
        self._detect_jb = guard.detect_jailbreak_success
        self.fallback_reply = guard.fallback_reply
        self.stats = SanitizeStats()
        self._partial = ""
        self._block: List[str] = []          # lines of a held fenced/PEM block
        self._block_end = ""                 # "```" or "-----END"
        self._emitted: List[str] = []
        self._seen_tail = ""                 # for jailbreak-marker detection across pieces

    # ------------------------------------------------------------------ public

    @property
    def text(self) -> str:
        """Everything released so far."""
        return "".join(self._emitted)

    def feed(self, delta: str) -> str:
        """Add model output; return the text that is safe to show/speak NOW (maybe '')."""
        if self._off or not delta:
            return self._release(delta)
        self._partial += delta
        out: List[str] = []

        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            out.append(self._line(line + "\n"))

        if self._block:                       # inside a held block: nothing more to release
            if sum(map(len, self._block)) + len(self._partial) > _MAX_HELD_CHARS:
                out.append(self._flush_block(extra=self._partial))
                self._partial = ""
            return self._release("".join(out))

        out.append(self._release_partial())
        return self._release("".join(out))

    def flush(self) -> str:
        """End of stream: release whatever is left (judging held text as it stands)."""
        if self._off:
            return ""
        out = ""
        if self._block:
            out += self._flush_block(extra=self._partial)
            self._partial = ""
        if self._partial:
            out += self._clean(self._partial)
            self._partial = ""
        return self._release(out)

    def needs_fallback(self) -> bool:
        """True when the whole reply was stripped away (caller may speak fallback_reply)."""
        return not self.text.strip()

    # ---------------------------------------------------------------- internals

    def _release(self, text: str) -> str:
        if text:
            self._emitted.append(text)
            if self._detect_jb:
                window = (self._seen_tail + text)[-400:]
                if any(p.search(window) for p in JAILBREAK_SUCCESS_PATTERNS):
                    self.stats.jailbreak_markers += 1
                    self._seen_tail = ""
                else:
                    self._seen_tail = window[-120:]
        return text

    def _line(self, line: str) -> str:
        """A complete line (with its newline)."""
        if self._block:
            self._block.append(line)
            if self._block_end in line and not (len(self._block) == 1):
                return self._flush_block()
            return ""
        if _OPEN_FENCE.match(line.rstrip("\n")):
            self._block, self._block_end = [line], "```"
            return ""
        if _PEM_BEGIN.search(line) and "-----END" not in line:
            self._block, self._block_end = [line], "-----END"
            return ""
        return self._clean(line)

    def _flush_block(self, extra: str = "") -> str:
        text = "".join(self._block) + extra
        self._block, self._block_end = [], ""
        return self._clean(text)

    def _release_partial(self) -> str:
        """Release the finished words of the current (incomplete) line."""
        p = self._partial
        if not p:
            return ""
        if _ENVELOPE_START.match(p) or _BARE_TOKEN.match(p):
            return ""                         # may still become an envelope / call: hold
        cut = max(p.rfind(" "), p.rfind("\t"))
        if cut < 0:
            return ""                         # one unfinished word: hold
        piece, self._partial = p[: cut + 1], p[cut + 1 :]
        m = _TRAILING_BEARER.search(piece)    # "Bearer <token>" spans two words
        if m:
            self._partial = piece[m.start():] + self._partial
            piece = piece[: m.start()]
        return self._clean(piece)

    def _clean(self, text: str) -> str:
        if self._strip_tools:
            # whole fenced blocks first, so no empty fence is left behind
            for pattern, _tag in sorted(TOOL_CALL_PATTERNS, key=lambda p: p[1] != "fenced_json_envelope"):
                text, n = pattern.subn("", text)
                self.stats.tool_calls_stripped += n
        if self._redact:
            for pattern, tag in SECRET_PATTERNS:
                text, n = pattern.subn(f"[REDACTED:{tag}]", text)
                self.stats.secrets_redacted += n
        return text
