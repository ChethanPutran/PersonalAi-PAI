"""Pick the capabilities worth showing the planner for one request.

The planner prompt lists every capability the user can reach (50+ with a few plugins), with full parameter
schemas. Most requests need two or three. ``select_capabilities`` ranks them against the goal and keeps the
top few, so the prompt shrinks and the planning call gets faster.

It is deliberately cheap and conservative:

* pure Python, no model, no I/O (microseconds, replayable offline against recorded traces);
* when it is not sure, it says so (``Selection.confident is False``) and the caller keeps the FULL list;
* a capability's siblings (``files.*``) travel together, so a plan that needs ``files.list`` before
  ``files.read`` is never cut in half;
* the caller still falls back to the full list if the plan names a capability that was not offered.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

# Always offered: replying, the things any request may end with ("remind me", "what's running"), and the shell,
# which plans use as the generic way to open or run anything a dedicated capability does not cover.
ALWAYS_PREFIXES = ("chat.", "schedule.", "task.", "shell.")

# Words people use that the capability text does not. Keys are capability families (the part before the
# dot) or full ids; values are extra words that point at them.
SYNONYMS: Dict[str, str] = {
    "camera": "photo picture selfie snap image shoot webcam video record capture",
    "screen": "screenshot screen capture display",
    "files": "file folder directory document documents download downloads path copy move delete rename "
             "save read write list pdf txt home desktop",
    "browser": "web website url link google search internet page navigate site online book fill form",
    "app": "application launch open start close quit program run viewer",
    "audio": "sound music play speak say volume loud quiet mute voice",
    "media": "music song video playlist pause resume next previous skip track play",
    "notifications": "notification notify alert popup message send dismiss",
    "clipboard": "clipboard copy paste copied",
    "contacts": "contact contacts person phone number call email friend name",
    "location": "location where gps place coordinates map address nearby",
    "device": "device battery info system status storage memory cpu specs",
    "flashlight": "flashlight torch light lamp",
    "keyboard": "keyboard type typing key press keystroke",
    "mouse": "mouse cursor click move scroll pointer",
    "input": "input control keyboard mouse remote",
    "shell": "shell terminal command run script bash exec execute install process xdg viewer",
    "computer": "click tap type swipe scroll screen control ui interface press",
    "agent": "agent background autonomous long running monitor watch keep working",
    "phone": "phone call dial sms text message ring",
    "desktop": "desktop wallpaper window windows workspace",
}

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the to of in on at for and or is it me my i you your do does did can could would please "
    "this that with from by be as are was what how when where who which up out into then also just "
    "some any all".split()
)


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> List[str]:
    return [_stem(w) for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1]


def _family(cap_id: str) -> str:
    return cap_id.split(".", 1)[0]


def _capability_words(cap_id: str, entry: Mapping[str, Any]) -> Dict[str, float]:
    """Weighted bag of words describing one capability."""
    words: Dict[str, float] = {}

    def add(text: str, weight: float) -> None:
        for t in tokens(text):
            words[t] = max(words.get(t, 0.0), weight)

    add(cap_id.replace(".", " ").replace("_", " "), 3.0)
    add(SYNONYMS.get(cap_id, "") + " " + SYNONYMS.get(_family(cap_id), ""), 2.0)
    add(str(entry.get("description") or ""), 1.0)
    params = entry.get("parameters")
    props = params.get("properties") if isinstance(params, dict) else None
    if isinstance(props, dict):
        add(" ".join(str(k) for k in props), 0.5)
    return words


@dataclass(frozen=True)
class Selection:
    ids: List[str]                       # capabilities to show the planner (sorted)
    confident: bool                      # False -> show everything
    reason: str = ""
    scores: Dict[str, float] = field(default_factory=dict)


def select_capabilities(
    goal: str,
    capabilities: Mapping[str, Mapping[str, Any]],
    *,
    top_families: int = 4,
    min_total: int = 12,
    sticky: Iterable[str] = (),
    min_score: float = 2.0,
) -> Selection:
    """Choose which of ``capabilities`` to show the planner for ``goal``.

    ``sticky`` ids (e.g. what the previous turn used) are always kept, so "do that again" works.
    With ``min_total`` or fewer capabilities in all there is nothing worth cutting.
    """
    all_ids = sorted(capabilities)
    if len(all_ids) <= min_total:
        return Selection(all_ids, False, "few_capabilities")

    goal_tokens = set(tokens(goal))
    if len(goal_tokens) < 2:
        # "again", "yes", "do it": the meaning is in the history, not the words.
        return Selection(all_ids, False, "too_short")

    # Inverse document frequency over capability descriptions: "file" in 8 of 50 matters less than "torch".
    bags = {cid: _capability_words(cid, capabilities[cid]) for cid in all_ids}
    df: Dict[str, int] = {}
    for bag in bags.values():
        for w in bag:
            df[w] = df.get(w, 0) + 1
    n = len(all_ids)

    scores: Dict[str, float] = {}
    for cid, bag in bags.items():
        s = 0.0
        for w in goal_tokens:
            weight = bag.get(w)
            if weight:
                s += weight * (1.0 + math.log((n + 1) / (df[w] + 0.5)))
        if s:
            scores[cid] = s

    ranked = sorted(scores, key=lambda c: (-scores[c], c))
    if not ranked or scores[ranked[0]] < min_score:
        return Selection(all_ids, False, "no_match", scores)

    # Keep the best few FAMILIES (plugins), with every sibling that matched anything: a plan that needs
    # files.list before files.read is never cut in half, and one plugin cannot crowd out the others.
    families: List[str] = []
    for cid in ranked:
        if _family(cid) not in families:
            families.append(_family(cid))
        if len(families) >= top_families:
            break
    chosen: Set[str] = {cid for cid in all_ids if _family(cid) in families}
    for cid in all_ids:
        if cid.startswith(ALWAYS_PREFIXES):
            chosen.add(cid)
    chosen.update(c for c in sticky if c in capabilities)

    if len(chosen) >= len(all_ids) - 2:
        return Selection(all_ids, False, "nothing_to_cut", scores)
    return Selection(sorted(chosen), True, "ranked", scores)


def restrict(capabilities: Mapping[str, Any], selection: Selection) -> Dict[str, Any]:
    """The capabilities to show: only the selected ones when the selection is confident."""
    if not selection.confident:
        return dict(capabilities)
    keep = set(selection.ids)
    return {cid: entry for cid, entry in capabilities.items() if cid in keep}


def missing_from(task_capabilities: Iterable[Optional[str]], shown: Mapping[str, Any]) -> List[str]:
    """Capabilities a plan names that were not offered (the signal to re-plan with everything)."""
    return sorted({c for c in task_capabilities if c and c not in shown})
