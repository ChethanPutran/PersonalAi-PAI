"""Streaming text -> speakable segments.

TTS sounds natural per sentence/clause and bad per token, but waiting for a whole reply
adds seconds of silence. So: release the FIRST segment early (first clause once it is long
enough), then one segment per sentence. Also cleans text for the ear (no markdown, URLs,
emoji, code) — the model is told not to produce them, this is the safety net.
"""
from __future__ import annotations

import re
from typing import List

_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd", "no",
           "e.g", "i.e", "u.s", "u.k", "a.m", "p.m", "approx", "fig"}
_SENTENCE_END = re.compile(r"([.!?…]+[\"')\]]*)(\s+|$)")
_CLAUSE_END = re.compile(r"([,;:—–])(\s+)")
_MAX_SEGMENT = 240

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_URL = re.compile(r"https?://\S+|www\.\S+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(\S(?:.*?\S)?)\1")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️‍]")
_WS = re.compile(r"\s+")


def speakable(text: str) -> str:
    """Make text fit for a voice: drop markdown/URLs/code/emoji, keep the words."""
    text = _CODE_BLOCK.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMOJI.sub("", text)
    text = text.replace("*", "").replace("`", "").replace("#", "")
    return _WS.sub(" ", text).strip()


def _is_boundary_dot(buf: str, pos: int) -> bool:
    """Is the '.' at buf[pos] a real sentence end (not a decimal / abbreviation)?"""
    if buf[pos] != ".":
        return True
    before = re.search(r"([A-Za-z.]+)$", buf[:pos])
    word = (before.group(1).lower() if before else "")
    if word in _ABBREV or (len(word) == 1 and word.isalpha()):      # "Dr." / "U." (initials)
        return False
    nxt = buf[pos + 1 : pos + 2]
    return not (nxt.isdigit() and pos > 0 and buf[pos - 1].isdigit())  # 3.5


class SpeechChunker:
    """feed(delta) -> segments to speak now; flush() at end of stream."""

    def __init__(self, first_chunk_min_chars: int = 24, min_segment_chars: int = 12) -> None:
        self.first_min = first_chunk_min_chars
        self.min_segment = min_segment_chars
        self._buf = ""
        self._emitted_any = False

    def feed(self, delta: str) -> List[str]:
        self._buf += delta
        out: List[str] = []
        while True:
            seg = self._take()
            if seg is None:
                break
            out.append(seg)
        return out

    def flush(self) -> List[str]:
        rest, self._buf = self._buf.strip(), ""
        if not rest:
            return []
        self._emitted_any = True
        return [rest]

    # ------------------------------------------------------------------
    def _take(self) -> "str | None":
        buf = self._buf
        # 1. sentence end
        for m in _SENTENCE_END.finditer(buf):
            end = m.end(1)
            if m.end(2) == len(buf) and not buf.endswith(("\n", " ")) and m.group(2) == "":
                continue                                   # '.' at the very end: more may follow ("3.5")
            if not _is_boundary_dot(buf, m.start(1)):
                continue
            seg = buf[:end].strip()
            if len(seg) < self.min_segment and m.end(2) < len(buf):
                continue                                   # too short: merge with the next sentence
            return self._cut(m.end(2) if m.end(2) else end, seg)
        # a line break is always a boundary
        nl = buf.find("\n")
        if nl > 0 and buf[:nl].strip():
            return self._cut(nl + 1, buf[:nl].strip())
        # 2. low-latency first segment: first clause, once long enough
        if not self._emitted_any and len(buf) >= self.first_min:
            for m in _CLAUSE_END.finditer(buf):
                if m.end(1) >= self.first_min:
                    return self._cut(m.end(2), buf[: m.end(1)].strip())
        # 3. safety cap: never hold an endless run-on
        if len(buf) > _MAX_SEGMENT:
            cut = buf.rfind(" ", 0, _MAX_SEGMENT)
            cut = cut if cut > 0 else _MAX_SEGMENT
            return self._cut(cut, buf[:cut].strip())
        return None

    def _cut(self, upto: int, segment: str) -> "str | None":
        self._buf = self._buf[upto:]
        if not segment:
            return self._take()
        self._emitted_any = True
        return segment
