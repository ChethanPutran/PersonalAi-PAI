"""Split extracted text into chunks of at most ~2000 characters (about 500 tokens) with a small overlap.

Splits on paragraph, then sentence/line, then word boundaries. Chunks never cross a page. Empty and boilerplate
chunks (no real words, mostly punctuation, exact repeats within a file) are dropped.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Iterator, List, Optional, Set, Tuple

MAX_CHARS = 2000            # the backend refuses a longer chunk
OVERLAP_CHARS = 150
MIN_ALNUM = 15

_PARA = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t\f\v ]+")

Segment = Tuple[Optional[int], str]


def clean(text: str) -> str:
    return _SPACES.sub(" ", _CTRL.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")))


def _hard_split(piece: str, limit: int) -> Iterator[str]:
    while len(piece) > limit:
        cut = piece.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        yield piece[:cut].strip()
        piece = piece[cut:].strip()
    if piece:
        yield piece


def _units(text: str, limit: int) -> Iterator[str]:
    """Pieces no longer than ``limit``: paragraphs, else sentences/lines, else word runs."""
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= limit:
            yield para
            continue
        cur = ""
        for sent in _SENTENCE.split(para):
            sent = sent.strip()
            if not sent:
                continue
            for part in _hard_split(sent, limit):
                if cur and len(cur) + 1 + len(part) > limit:
                    yield cur
                    cur = part
                else:
                    cur = f"{cur} {part}" if cur else part
        if cur:
            yield cur


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0:
        return ""
    if len(text) <= overlap:
        return text
    tail = text[-overlap:]
    cut = tail.find(" ")
    return tail[cut + 1:] if 0 <= cut < len(tail) - 1 else tail


def _boilerplate(text: str) -> bool:
    alnum = sum(1 for c in text if c.isalnum())
    return alnum < MIN_ALNUM or alnum / max(len(text), 1) < 0.25


def chunk_text(text: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> Iterator[str]:
    """Chunks of one block of text (no page handling, no filtering)."""
    overlap = min(overlap, max_chars // 4)
    cur, prefix = "", ""
    for unit in _units(clean(text), max_chars - overlap - 1):
        if cur and len(prefix) + len(cur) + 1 + len(unit) > max_chars:
            yield (prefix + " " + cur).strip() if prefix else cur
            prefix = _tail(cur, overlap)
            cur = unit
        else:
            cur = f"{cur}\n{unit}" if cur else unit
    if cur:
        yield (prefix + " " + cur).strip() if prefix else cur


def chunk_segments(segments: Iterable[Segment], *, max_chars: int = MAX_CHARS,
                   overlap: int = OVERLAP_CHARS) -> Iterator[Tuple[str, Optional[int]]]:
    """(text, page) chunks from (page, text) segments; lazily, so the caller can stop early."""
    seen: Set[bytes] = set()
    for page, text in segments:
        for chunk in chunk_text(text, max_chars=max_chars, overlap=overlap):
            chunk = chunk[:max_chars]
            if _boilerplate(chunk):
                continue
            digest = hashlib.blake2b(chunk.encode("utf-8", "replace"), digest_size=8).digest()
            if digest in seen:
                continue
            seen.add(digest)
            yield chunk, page
