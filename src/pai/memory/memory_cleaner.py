import re

# Same patterns as `memory.manager._looks_like_retry`. Kept local so
# decomposer has no import dependency on the memory package just for
# intent detection.
_RETRY_PATTERNS = [
    re.compile(r"\bretry\b", re.I),
    re.compile(r"\btry\s+again\b", re.I),
    re.compile(r"\bagain\b", re.I),
    re.compile(r"\bredo\b", re.I),
    re.compile(r"\brepeat\b", re.I),
    re.compile(r"\bdo\s+(?:it|that)\s+again\b", re.I),
    re.compile(r"\bcontinue\b", re.I),
    re.compile(r"\bsame\b", re.I),
    re.compile(r"\bwhy\s+did\s+(?:it|that|the\s+last\s+\w+)\s+fail\b", re.I),
    re.compile(r"\bwhat\s+happened\b", re.I),
    re.compile(r"\b(?:didn'?t|did\s+not)\s+work\b", re.I),
]


def _looks_like_retry(text: str) -> bool:
    return any(p.search(text) for p in _RETRY_PATTERNS)