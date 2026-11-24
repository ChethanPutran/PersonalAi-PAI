"""RecallSanitizer — defense in depth on the READ path.

Data can reach storage without passing today's guard (written by an older
version, restored from backup, or poisoned out-of-band). So everything is
re-checked before it can enter a prompt: quarantined items are dropped, items
that now score as attacks are dropped, the rest are neutralized and truncated.
"""
from __future__ import annotations

from typing import Optional

from pai.config.models.memory import MemorySafetyConfig, MemorySafetyMode
from pai.memory.safety.models import Trust
from pai.memory.safety.scanner import MemoryScanner


class RecallSanitizer:
    def __init__(self, config: Optional[MemorySafetyConfig] = None,
                 scanner: Optional[MemoryScanner] = None) -> None:
        self.config = config or MemorySafetyConfig()
        self.scanner = scanner or MemoryScanner()
        self.dropped_quarantined = 0
        self.dropped_risky = 0

    def clean(self, text: str, *, trust: Trust = Trust.UNTRUSTED) -> Optional[str]:
        """Sanitized text safe to place in a prompt as DATA, or None to drop it."""
        if trust == Trust.QUARANTINED:
            self.dropped_quarantined += 1
            return None
        if self.config.mode == MemorySafetyMode.OFF:
            return text[: self.config.max_recall_chars_per_item]
        report = self.scanner.scan(text, max_chars=self.config.max_recall_chars_per_item * 4)
        if report.risk >= self.config.risk_threshold:
            self.dropped_risky += 1
            return None
        out = report.sanitized
        if len(out) > self.config.max_recall_chars_per_item:
            out = out[: self.config.max_recall_chars_per_item].rstrip() + "…"
        return out or None
