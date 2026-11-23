"""Turn what the USER said into durable facts (long-term preferences + graph).

Deliberately conservative — this is the one place where free text becomes
long-lived "knowledge", so it is the main poisoning surface:

  * only USER-authored text is ever passed in (never assistant or tool output);
  * rule-based with a closed vocabulary: no free-form keys, no generated Cypher;
  * statements only (questions and imperatives are ignored);
  * values are short, plain words — anything else is dropped, not "cleaned up";
  * facts that are not an explicit "remember ..." are marked ``explicit=False`` and
    get their importance capped downstream.

The result is typed (ExtractedFact); the MemoryGuard still vets every fact.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


class FactKind(StrEnum):
    PROFILE = "profile"          # who the user is (name, city, ...)
    LIKES = "likes"
    DISLIKES = "dislikes"
    PREFERS = "prefers"


class ExtractedFact(BaseModel):
    kind: FactKind
    key: str                     # plain identifier, e.g. "name", "likes.jazz"
    value: str                   # short plain text
    explicit: bool = False       # user said "remember ..."
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    evidence: str = ""           # the (short) sentence it came from


_VALUE = r"([A-Za-z][A-Za-z0-9 '\-]{1,60}?)"
_END = r"(?:[.!,;]|$)"
_QUESTION_START = re.compile(
    r"^\s*(?:who|what|when|where|why|how|which|do|does|did|can|could|would|will|is|are|should)\b", re.I)
_REMEMBER = re.compile(r"^\s*(?:please\s+)?remember\s+(?:that\s+)?", re.I)
_SPLIT = re.compile(r"(?<=[.!])\s+|\n+")

# (regex, kind, key-builder, value-group)
_PROFILE_KEYS = {
    "name": "name", "nickname": "nickname", "favorite color": "favorite_color",
    "favourite colour": "favorite_color", "favorite colour": "favorite_color",
    "favorite food": "favorite_food", "favourite food": "favorite_food",
    "timezone": "timezone", "time zone": "timezone", "language": "language",
    "city": "city", "job": "occupation", "occupation": "occupation",
}

_RULES = [
    (re.compile(rf"\b(?:my name is|i am called|i'm called|call me)\s+{_VALUE}{_END}", re.I), "name"),
    (re.compile(rf"\bi (?:live in|am from|'m from|reside in)\s+{_VALUE}{_END}", re.I), "city"),
    (re.compile(rf"\bmy\s+({'|'.join(map(re.escape, _PROFILE_KEYS))})\s+is\s+{_VALUE}{_END}", re.I), "profile"),
    (re.compile(rf"\bi\s+(?:really\s+)?(?:like|love|enjoy)\s+{_VALUE}{_END}", re.I), "likes"),
    (re.compile(rf"\bi\s+(?:really\s+)?(?:dislike|hate)\s+{_VALUE}{_END}", re.I), "dislikes"),
    (re.compile(rf"\bi\s+(?:do not|don't)\s+(?:really\s+)?like\s+{_VALUE}{_END}", re.I), "dislikes"),
    (re.compile(rf"\bi\s+prefer\s+{_VALUE}\s+(?:over|to|rather than)\s+{_VALUE}{_END}", re.I), "prefers2"),
    (re.compile(rf"\bi\s+prefer\s+{_VALUE}{_END}", re.I), "prefers"),
]

_MAX_FACTS = 5


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit]


_CLAUSE_WORDS = re.compile(r"\b(?:i|me|my|you|your|because|but|which|that|when|if|and\s+i)\b", re.I)


def _clean_value(value: str) -> Optional[str]:
    value = re.sub(r"\s+", " ", value).strip(" '-")
    if not (2 <= len(value) <= 60) or len(value.split()) > 5:
        return None
    if _CLAUSE_WORDS.search(value):          # a clause, not a plain value: drop, don't "fix"
        return None
    return value


class FactExtractor:
    def extract(self, user_text: str) -> List[ExtractedFact]:
        if not user_text:
            return []
        facts: List[ExtractedFact] = []
        seen: set[str] = set()
        for sentence in _SPLIT.split(user_text[:2000]):
            sentence = sentence.strip()
            if not sentence or sentence.endswith("?") or _QUESTION_START.match(sentence):
                continue
            explicit = bool(_REMEMBER.match(sentence))
            body = _REMEMBER.sub("", sentence, count=1)
            for pattern, rule in _RULES:
                m = pattern.search(body)
                if not m:
                    continue
                fact = self._build(rule, m, explicit, sentence)
                if fact and fact.key not in seen:
                    seen.add(fact.key)
                    facts.append(fact)
                break                       # one fact per sentence
            if len(facts) >= _MAX_FACTS:
                break
        return facts

    @staticmethod
    def _build(rule: str, m: "re.Match[str]", explicit: bool, sentence: str) -> Optional[ExtractedFact]:
        ev = sentence[:160]
        if rule == "profile":
            key, value = _PROFILE_KEYS[m.group(1).lower()], _clean_value(m.group(2))
            return ExtractedFact(kind=FactKind.PROFILE, key=key, value=value, explicit=explicit,
                                 confidence=0.9, evidence=ev) if value else None
        if rule in ("name", "city"):
            value = _clean_value(m.group(1))
            return ExtractedFact(kind=FactKind.PROFILE, key=rule, value=value, explicit=explicit,
                                 confidence=0.85, evidence=ev) if value else None
        if rule in ("likes", "dislikes"):
            value = _clean_value(m.group(1))
            if not value:
                return None
            kind = FactKind.LIKES if rule == "likes" else FactKind.DISLIKES
            return ExtractedFact(kind=kind, key=f"{rule}.{_slug(value)}", value=value,
                                 explicit=explicit, confidence=0.7, evidence=ev)
        if rule == "prefers2":
            chosen, other = _clean_value(m.group(1)), _clean_value(m.group(2))
            return ExtractedFact(kind=FactKind.PREFERS, key=f"prefers.{_slug(other)}", value=chosen,
                                 explicit=explicit, confidence=0.75, evidence=ev) if chosen and other else None
        value = _clean_value(m.group(1))
        return ExtractedFact(kind=FactKind.PREFERS, key=f"prefers.{_slug(value)}", value=value,
                             explicit=explicit, confidence=0.6, evidence=ev) if value else None
