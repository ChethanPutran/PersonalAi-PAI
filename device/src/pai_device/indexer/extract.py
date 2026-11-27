"""Plain text out of files. Never executes or renders anything; every result is untrusted text.

Supported: .txt .md .csv .log .json .rst, .docx (zip + XML from the standard library), .pdf (needs the optional
``pypdf``). Each extractor is a generator of ``(page, text)`` so the caller can stop early (chunk cap, deadline).
"""
from __future__ import annotations

import os
import stat
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import IO, Iterator, Optional, Tuple

TEXT_EXTS = {".txt", ".md", ".csv", ".log", ".json", ".rst"}
DOCX_EXT = ".docx"
PDF_EXT = ".pdf"
SUPPORTED = TEXT_EXTS | {DOCX_EXT, PDF_EXT}

MAX_TEXT_BYTES = 4 * 1024 * 1024        # read at most this much of a text file
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024   # decompressed word/document.xml (zip-bomb guard)
MAX_PDF_PAGES = 1000
PDF_INSTALL_HINT = "PDF files are skipped: install the optional dependency with  pip install 'pai-device[documents]'  (or pip install pypdf)"

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

Segment = Tuple[Optional[int], str]


class ExtractError(Exception):
    """A file that cannot be read as text. ``reason`` is a short stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


def pdf_available() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:
        return False


def open_regular(path: str) -> IO[bytes]:
    """Open without following a final symlink and only if it is a regular file (never a FIFO or device)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOCTTY", 0))
    except OSError as exc:
        raise ExtractError("unreadable", exc.strerror or str(exc)) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ExtractError("not_regular")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _check(deadline: Optional[float]) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise ExtractError("timeout", "extraction took too long")


def _text(fh: IO[bytes]) -> Iterator[Segment]:
    data = fh.read(MAX_TEXT_BYTES)
    yield None, data.decode("utf-8", errors="replace")


def _docx(fh: IO[bytes], deadline: Optional[float]) -> Iterator[Segment]:
    try:
        zf = zipfile.ZipFile(fh)
        info = zf.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ExtractError("extract_failed", "not a readable .docx") from exc
    if info.file_size > MAX_DOCX_XML_BYTES:
        raise ExtractError("too_large", "document body is too large")
    with zf.open(info) as part:
        raw = part.read(MAX_DOCX_XML_BYTES + 1)
    if len(raw) > MAX_DOCX_XML_BYTES:
        raise ExtractError("too_large", "document body is too large")
    try:
        root = ET.fromstring(raw)                     # the standard parser does not load external entities
    except ET.ParseError as exc:
        raise ExtractError("extract_failed", "bad document XML") from exc
    del raw
    for i, para in enumerate(root.iter(f"{_W}p")):
        if i % 200 == 0:
            _check(deadline)
        parts = []
        for el in para.iter():
            if el.tag == f"{_W}t" and el.text:
                parts.append(el.text)
            elif el.tag in (f"{_W}tab", f"{_W}br"):
                parts.append(" ")
        line = "".join(parts).strip()
        if line:
            yield None, line + "\n\n"


def _pdf(fh: IO[bytes], deadline: Optional[float]) -> Iterator[Segment]:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ExtractError("pdf_unsupported", PDF_INSTALL_HINT) from exc
    try:
        reader = PdfReader(fh)
        if reader.is_encrypted:
            raise ExtractError("encrypted", "password protected")
        pages = reader.pages
        count = min(len(pages), MAX_PDF_PAGES)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError("extract_failed", "not a readable PDF") from exc
    for n in range(count):
        _check(deadline)
        try:
            text = pages[n].extract_text() or ""
        except Exception:
            continue                                   # one bad page must not lose the others
        if text.strip():
            yield n + 1, text


def extract(path: str, *, deadline: Optional[float] = None) -> Iterator[Segment]:
    """Segments of text of ``path`` (a real, already boundary-checked path). Raises :class:`ExtractError`."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED:
        raise ExtractError("unsupported")
    if ext == PDF_EXT and not pdf_available():
        raise ExtractError("pdf_unsupported", PDF_INSTALL_HINT)
    fh = open_regular(path)
    try:
        if ext in TEXT_EXTS:
            gen = _text(fh)
        elif ext == DOCX_EXT:
            gen = _docx(fh, deadline)
        else:
            gen = _pdf(fh, deadline)
        yield from gen
    finally:
        fh.close()
