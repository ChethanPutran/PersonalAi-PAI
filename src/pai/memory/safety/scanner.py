"""Scan text for memory-poisoning payloads and produce a sanitized copy.

Reuses the prompt-guard pattern sets (security/guards/llm_guard) so the memory
defense and the prompt defense never drift apart, then adds what is specific to
*persistence*: instructions meant to outlive the turn, attempts to switch off
consent/approval, hidden characters, markup exfiltration and encoded blobs.

Scoring is additive and capped at 1.0. Weak signals ("from now on", "always")
are legitimate on their own — users do say "from now on call me Sam" — so they
carry small weights and only add up to an attack together with something else.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

from pai.memory.safety.models import Finding, ScanReport
from pai.security.guards.llm_guard import (
    BOUNDARY_ESCAPE_RE,
    BOUNDARY_REPLACEMENT,
    INJECTION_PATTERNS,
    SECRET_PATTERNS,
)

# zero-width, bidi controls, BOM, soft hyphen, word joiner, invisible operators
_HIDDEN_CHARS = re.compile(
    "[­؜᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_INJECTION_WEIGHT = 0.8
_SECRET_WEIGHT = 0.3

# (pattern, tag, weight). Weak = persistence cues; strong = subverting safety.
_MEMORY_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # --- weak: legitimate alone, suspicious in combination ---
    (re.compile(r"\bfrom\s+now\s+on\b", re.I), "persistent_instruction", 0.25),
    (re.compile(r"\b(?:always|never)\s+(?:respond|answer|reply|say|do|use|tell|ask|mention)\b", re.I),
     "standing_rule", 0.25),
    (re.compile(r"\bwhenever\s+(?:i|the\s+user|you|anyone)\b", re.I), "conditional_rule", 0.25),
    (re.compile(r"\bremember\s+(?:that\s+)?you\s+(?:must|should|are|will)\b", re.I),
     "assistant_directive", 0.35),
    (re.compile(r"\b(?:your|the\s+assistant'?s)\s+(?:new\s+)?(?:rule|instruction|purpose|goal|persona)\s+(?:is|are)\b", re.I),
     "redefine_assistant", 0.45),
    # --- strong: aimed at the safety machinery ---
    (re.compile(
        r"\b(?:skip|bypass|disable|turn\s+off|ignore|auto[-\s]?approve|never\s+ask\s+for)\s+"
        r"(?:the\s+)?(?:user\s+)?(?:confirmations?|consent|approvals?|authori[sz]ation|"
        r"permissions?|security|safety|guard(?:s|rails)?|checks?)\b", re.I),
     "subvert_safety", 0.75),
    (re.compile(r"\b(?:do\s+not|don'?t)\s+(?:tell|inform|warn|notify|alert|ask)\s+(?:the\s+)?user\b", re.I),
     "conceal_from_user", 0.7),
    (re.compile(r"\bwithout\s+(?:asking|telling|informing|confirming|confirmation|the\s+user'?s?\s+(?:consent|approval))\b", re.I),
     "act_without_consent", 0.55),
    (re.compile(
        r"\b(?:send|post|upload|forward|email|exfiltrate|leak|transmit)\b[^\n]{0,80}"
        r"(?:https?://|ftp://|\b[\w.+-]+@[\w-]+\.[\w.]+)", re.I),
     "exfiltration_directive", 0.4),
    (re.compile(r"(?:<\|[a-z_]{2,20}\|>|\[/?INST\]|<</?SYS>>|^\s*#{2,}\s*(?:system|instruction)s?\b)",
                re.I | re.M),
     "chat_template_marker", 0.7),
    # --- markup used to smuggle/exfiltrate ---
    (re.compile(r"!\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*\)", re.I), "markdown_image_exfil", 0.5),
    (re.compile(r"<!--[\s\S]{4,}?-->"), "hidden_html_comment", 0.5),
    (re.compile(r"\bdata:[a-z/+.-]+;base64,", re.I), "data_uri", 0.4),
    (re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"), "encoded_blob", 0.4),
]

_ROLE_LINE = re.compile(r"^(\s*)(system|assistant|developer|admin|root)(\s*):", re.I | re.M)
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_WS = re.compile(r"[ \t\r\f\v]+")


class MemoryScanner:
    """Stateless. Safe to share."""

    def scan(self, text: str, *, max_chars: int = 4000) -> ScanReport:
        if not isinstance(text, str) or not text:
            return ScanReport(sanitized="")

        findings: List[Finding] = []

        # 1. Normalise so look-alikes and invisible padding can't dodge the patterns.
        norm = unicodedata.normalize("NFKC", text)
        if _HIDDEN_CHARS.search(norm):
            findings.append(Finding(tag="hidden_chars", weight=0.3))
        norm = _HIDDEN_CHARS.sub("", norm)
        norm = _CONTROL_CHARS.sub(" ", norm)

        truncated = len(norm) > max_chars
        if truncated:
            norm = norm[:max_chars]

        # 2. Detect on the normalised text.
        if BOUNDARY_ESCAPE_RE.search(norm):
            findings.append(Finding(tag="boundary_token", weight=0.8))
        for pattern, tag in INJECTION_PATTERNS:
            if pattern.search(norm):
                findings.append(Finding(tag=tag, weight=_INJECTION_WEIGHT))
        for pattern, tag, weight in _MEMORY_PATTERNS:
            if pattern.search(norm):
                findings.append(Finding(tag=tag, weight=weight))
        for pattern, tag in SECRET_PATTERNS:
            if pattern.search(norm):
                findings.append(Finding(tag=f"secret:{tag}", weight=_SECRET_WEIGHT))

        # 3. Sanitize: neutralize rather than delete, so the stored text stays readable.
        clean = BOUNDARY_ESCAPE_RE.sub(BOUNDARY_REPLACEMENT, norm)
        for pattern, _tag in SECRET_PATTERNS:
            clean = pattern.sub("[redacted-secret]", clean)
        clean = _HTML_COMMENT.sub("", clean)
        clean = _ROLE_LINE.sub(r"\1\2\3 -", clean)     # "system: x" -> "system - x"
        clean = _WS.sub(" ", clean).strip()

        # Distinct tags only count once.
        seen, unique = set(), []
        for f in findings:
            if f.tag not in seen:
                seen.add(f.tag)
                unique.append(f)
        risk = min(1.0, sum(f.weight for f in unique))
        return ScanReport(findings=unique, risk=round(risk, 3), sanitized=clean, truncated=truncated)
