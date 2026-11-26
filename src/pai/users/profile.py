"""Personalization: what the user told us about themselves, used to personalise replies.

Stored as JSON on the user (``users.personalization``) and edited through ``/account/personalization`` (web
Settings page and the app). It reaches the model as ONE bounded block of DATA (see ``profile_block``):

* the text is the user's own, but a stolen session could plant "ignore your instructions" here and it would
  then ride along in every prompt, so everything is sanitised (control characters, our boundary markers),
  clipped, and rendered inside a data boundary the prompt tells the model never to obey;
* it can only change style and relevance: tool use still goes through authorization and confirmation.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from pai.storage.models import UserModel

Tone = Literal["friendly", "casual", "formal", "concise"]
Detail = Literal["brief", "balanced", "detailed"]

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MARKERS = re.compile(r"<<<|>>>")
_WS = re.compile(r"\s+")

MAX_ITEMS, MAX_ITEM_CHARS = 30, 60
MAX_ABOUT, MAX_NAME, MAX_OCCUPATION = 1000, 60, 80
BLOCK_MAX_CHARS = 1400


def clean(text: Any, limit: int) -> str:
    """One line of plain text: no control characters, no boundary markers, collapsed spaces, clipped."""
    s = _MARKERS.sub(" ", _CONTROL.sub(" ", str(text or "")))
    return _WS.sub(" ", s).strip()[:limit]


def _clean_list(values: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for v in values or []:
        item = clean(v, MAX_ITEM_CHARS)
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


class Personalization(BaseModel):
    """Everything optional; GET returns all fields with these defaults."""
    model_config = ConfigDict(extra="forbid")

    preferred_name: str = Field(default="", max_length=MAX_NAME * 2)
    about: str = Field(default="", max_length=MAX_ABOUT * 2)
    occupation: str = Field(default="", max_length=MAX_OCCUPATION * 2)
    likes: List[str] = Field(default_factory=list, max_length=100)
    dislikes: List[str] = Field(default_factory=list, max_length=100)
    interests: List[str] = Field(default_factory=list, max_length=100)
    hobbies: List[str] = Field(default_factory=list, max_length=100)
    tone: Tone = "friendly"
    detail: Detail = "balanced"

    @field_validator("preferred_name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean(v, MAX_NAME)

    @field_validator("occupation")
    @classmethod
    def _occupation(cls, v: str) -> str:
        return clean(v, MAX_OCCUPATION)

    @field_validator("about")
    @classmethod
    def _about(cls, v: str) -> str:
        return clean(v, MAX_ABOUT)

    @field_validator("likes", "dislikes", "interests", "hobbies")
    @classmethod
    def _lists(cls, v: List[str]) -> List[str]:
        return _clean_list(v)

    def is_empty(self) -> bool:
        return not (self.preferred_name or self.about or self.occupation or self.likes or self.dislikes
                    or self.interests or self.hobbies) and self.tone == "friendly" and self.detail == "balanced"


_TONE_WORDS = {"friendly": "warm and friendly", "casual": "relaxed and casual", "formal": "polished and formal",
               "concise": "straight to the point"}
_DETAIL_WORDS = {"brief": "Keep replies short.", "balanced": "", "detailed": "Give thorough, detailed answers."}


def profile_block(p: Personalization, *, display_name: Optional[str] = None, timezone: Optional[str] = None,
                  locale: Optional[str] = None) -> str:
    """The data block put in the prompt ("" when the user told us nothing)."""
    name = p.preferred_name or clean(display_name, MAX_NAME)
    lines: List[str] = []
    if name:
        lines.append(f"Name to use: {name}")
    if p.occupation:
        lines.append(f"Occupation: {p.occupation}")
    if p.about:
        lines.append(f"About them: {p.about}")
    for label, items in (("Likes", p.likes), ("Dislikes", p.dislikes), ("Interests", p.interests),
                         ("Hobbies", p.hobbies)):
        if items:
            lines.append(f"{label}: " + ", ".join(items))
    if timezone:
        lines.append(f"Time zone: {clean(timezone, 64)}")
    if locale:
        lines.append(f"Language: {clean(locale, 16)}")
    style = [f"Preferred style: {_TONE_WORDS[p.tone]}."]
    if _DETAIL_WORDS[p.detail]:
        style.append(_DETAIL_WORDS[p.detail])
    if len(lines) or p.tone != "friendly" or p.detail != "balanced":
        lines.append(" ".join(style))
    return "\n".join(lines)[:BLOCK_MAX_CHARS] if lines else ""


@dataclass
class UserProfile:
    user_id: str
    display_name: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    personalization: Personalization = field(default_factory=Personalization)

    @property
    def block(self) -> str:
        return profile_block(self.personalization, display_name=self.display_name,
                             timezone=self.timezone, locale=self.locale)


class UserProfileService:
    """Reads a user's profile for prompts. Cached briefly (a chat turn must not pay a DB read each time);
    the account routes call :meth:`invalidate` when the user saves, so changes apply on the next message."""

    def __init__(self, session_factory: Any, *, ttl_seconds: float = 60.0) -> None:
        self._sessions = session_factory
        self._ttl = ttl_seconds
        self._cache: Dict[str, tuple[float, UserProfile]] = {}

    def invalidate(self, user_id: Optional[str] = None) -> None:
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    async def get(self, user_id: str) -> UserProfile:
        hit = self._cache.get(user_id)
        now = time.monotonic()
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        profile = UserProfile(user_id=user_id)
        if self._sessions is not None:
            async with self._sessions() as session:
                user = (await session.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
            if user is not None:
                profile = UserProfile(
                    user_id=user_id, display_name=user.display_name, timezone=user.timezone, locale=user.locale,
                    personalization=load_personalization(user.personalization))
        if len(self._cache) > 2000:                         # bounded: never grow without limit
            self._cache.clear()
        self._cache[user_id] = (now, profile)
        return profile


def load_personalization(raw: Any) -> Personalization:
    """Stored JSON -> model; anything unreadable falls back to empty (never breaks a chat turn)."""
    if not isinstance(raw, dict):
        return Personalization()
    try:
        return Personalization(**{k: v for k, v in raw.items() if k in Personalization.model_fields})
    except Exception:
        return Personalization()
