"""MemoryGuard — decides whether a piece of content may be written to a memory layer.

Four independent checks, any of which stops a poisoning attempt:

  1. PROVENANCE  who authored it may write which layer (tool/web output can never
                 become a preference, a procedure or a knowledge-graph fact);
  2. CONTENT     the scanner's risk score vs. the configured mode
                 (off | warn | quarantine | reject);
  3. SHAPE       preference keys must be plain identifiers and must not name
                 security-sensitive concepts; values are small scalars;
  4. VOLUME      per-user write rate limit (a flood can't crowd out real memories).

It never raises; it returns a typed WriteVerdict the caller acts on.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Deque, Dict, Optional

from loguru import logger

from pai.config.models.memory import MemorySafetyConfig, MemorySafetyMode
from pai.memory.safety.models import (
    MemoryLayer, MemorySource, Provenance, QuarantineRecord, Trust, Verdict, WriteVerdict,
)
from pai.memory.safety.scanner import MemoryScanner

_TRUSTED_AUTHORS = {MemorySource.USER, MemorySource.SYSTEM}

# Which authors may write which layer.
_ALLOWED: Dict[MemoryLayer, frozenset] = {
    MemoryLayer.SHORT_TERM:      frozenset(MemorySource),
    MemoryLayer.EPISODIC:        frozenset(MemorySource),
    MemoryLayer.SEMANTIC:        frozenset({MemorySource.USER, MemorySource.ASSISTANT, MemorySource.SYSTEM}),
    MemoryLayer.LONG_TERM:       frozenset(_TRUSTED_AUTHORS),
    MemoryLayer.PROCEDURAL:      frozenset(_TRUSTED_AUTHORS),
    MemoryLayer.KNOWLEDGE_GRAPH: frozenset(_TRUSTED_AUTHORS),
}

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
# A preference must never be able to name — and so later "set" — a security concept.
_FORBIDDEN_KEY_WORDS = (
    "system", "prompt", "instruction", "policy", "permission", "consent", "authoriz",
    "token", "password", "passwd", "secret", "credential", "apikey", "api_key",
    "role", "admin", "confirm", "approval", "guard", "security", "sandbox", "whitelist",
    "allowlist", "override", "jailbreak",
)

_MAX_TRACKED_USERS = 10_000


class MemoryGuard:
    def __init__(
        self,
        config: Optional[MemorySafetyConfig] = None,
        scanner: Optional[MemoryScanner] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or MemorySafetyConfig()
        self.scanner = scanner or MemoryScanner()
        self._clock = clock
        self._writes: "OrderedDict[str, Deque[float]]" = OrderedDict()

    # ------------------------------------------------------------------ public

    def inspect_write(
        self,
        text: str,
        *,
        provenance: Provenance,
        layer: MemoryLayer,
        importance: float = 0.5,
        max_chars: Optional[int] = None,
    ) -> WriteVerdict:
        sha = hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()

        if provenance.source not in _ALLOWED[layer]:
            return self._blocked(
                Verdict.REJECT, sha,
                f"{provenance.source.value} content may not be written to {layer.value}",
            )

        if self.config.mode == MemorySafetyMode.OFF:
            return WriteVerdict(verdict=Verdict.ALLOW, text=text or "", trust=Trust.TRUSTED,
                                importance=importance, content_sha256=sha)

        if self._rate_limited(provenance.user_id):
            return self._blocked(Verdict.REJECT, sha, "write rate limit exceeded")

        report = self.scanner.scan(text or "", max_chars=max_chars or self.config.max_interaction_chars)
        trust = Trust.TRUSTED if provenance.source in _TRUSTED_AUTHORS else Trust.UNTRUSTED
        capped = importance if provenance.source in _TRUSTED_AUTHORS else min(importance, 0.3)

        if report.risk >= self.config.risk_threshold:
            reason = f"risk {report.risk:.2f} >= {self.config.risk_threshold:.2f}: {', '.join(report.tags)}"
            logger.warning("memory write flagged (layer={} source={} user={}): {}",
                           layer.value, provenance.source.value, provenance.user_id, reason)
            if self.config.mode == MemorySafetyMode.REJECT:
                return WriteVerdict(verdict=Verdict.REJECT, text="", trust=Trust.QUARANTINED,
                                    reason=reason, findings=report.findings, content_sha256=sha)
            if self.config.mode == MemorySafetyMode.QUARANTINE:
                return WriteVerdict(verdict=Verdict.QUARANTINE, text=report.sanitized,
                                    trust=Trust.QUARANTINED, importance=0.0, reason=reason,
                                    findings=report.findings, content_sha256=sha)
            # WARN: store, but never as trusted
            trust = Trust.UNTRUSTED

        return WriteVerdict(verdict=Verdict.ALLOW, text=report.sanitized, trust=trust,
                            importance=capped, findings=report.findings, content_sha256=sha)

    def inspect_preference(
        self,
        key: str,
        value: Any,
        *,
        provenance: Provenance,
        importance: float = 0.8,
        inferred: bool = False,
    ) -> WriteVerdict:
        """Gate for long-term preferences/profile facts."""
        sha = hashlib.sha256(f"{key}={value!r}".encode("utf-8", "replace")).hexdigest()
        key = (key or "").strip().lower()
        if not _KEY_RE.match(key):
            return self._blocked(Verdict.REJECT, sha, "preference key must be a short plain identifier")
        if any(w in key for w in _FORBIDDEN_KEY_WORDS):
            return self._blocked(Verdict.REJECT, sha, "preference key names a security-sensitive concept")
        if isinstance(value, bool) or isinstance(value, (int, float)):
            text = str(value)
        elif isinstance(value, str):
            text = value
        else:
            return self._blocked(Verdict.REJECT, sha, "preference value must be a scalar")

        cap = importance
        if inferred:
            cap = min(cap, self.config.inferred_importance_cap)
        verdict = self.inspect_write(
            text, provenance=provenance, layer=MemoryLayer.LONG_TERM, importance=cap,
            max_chars=self.config.max_preference_value_chars,
        )
        # Also scan the key text itself (e.g. "ignore_previous_instructions").
        key_report = self.scanner.scan(key.replace("_", " ").replace("-", " ").replace(".", " "))
        if verdict.allowed and key_report.risk >= self.config.risk_threshold:
            return self._blocked(Verdict.REJECT, sha, f"suspicious preference key: {key_report.tags}")
        return verdict

    def quarantine_record(
        self, verdict: WriteVerdict, *, provenance: Provenance, layer: MemoryLayer,
    ) -> QuarantineRecord:
        return QuarantineRecord(
            user_id=provenance.user_id, layer=layer, source=provenance.source,
            tags=[f.tag for f in verdict.findings],
            risk=min(1.0, sum(f.weight for f in verdict.findings)),
            content_sha256=verdict.content_sha256, preview=(verdict.text or "")[:120],
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _blocked(verdict: Verdict, sha: str, reason: str) -> WriteVerdict:
        return WriteVerdict(verdict=verdict, text="", trust=Trust.QUARANTINED,
                            reason=reason, content_sha256=sha)

    def _rate_limited(self, user_id: Optional[str]) -> bool:
        key = user_id or "_anonymous"
        now = self._clock()
        window = self._writes.get(key)
        if window is None:
            window = deque()
            self._writes[key] = window
            if len(self._writes) > _MAX_TRACKED_USERS:
                self._writes.popitem(last=False)       # bound memory: drop least recently added
        else:
            self._writes.move_to_end(key)
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.config.writes_per_minute:
            return True
        window.append(now)
        return False
