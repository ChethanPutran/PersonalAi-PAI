from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class MemoryRecord:
    role: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"conversations": [], "facts": [], "notes": []}

    def _save(self) -> None:
        serializable = {
            "conversations": [
                {
                    "user": asdict(item["user"]) if isinstance(item["user"], MemoryRecord) else item["user"],
                    "assistant": asdict(item["assistant"]) if isinstance(item["assistant"], MemoryRecord) else item["assistant"],
                }
                for item in self._data["conversations"]
            ],
            "facts": self._data["facts"],
            "notes": self._data["notes"],
        }
        self.path.write_text(json.dumps(serializable, indent=2))

    def add_turn(self, user_input: str, assistant_output: str, metadata: Dict[str, Any] | None = None) -> None:
        self._data["conversations"].append(
            {
                "user": asdict(MemoryRecord(role="user", content=user_input, metadata=metadata or {})),
                "assistant": asdict(MemoryRecord(role="assistant", content=assistant_output, metadata=metadata or {})),
            }
        )
        self._data["conversations"] = self._data["conversations"][-100:]
        self._save()

    def add_fact(self, fact: str, tags: List[str] | None = None) -> None:
        self._data["facts"].append(
            {"fact": fact, "tags": tags or [], "timestamp": datetime.utcnow().isoformat()}
        )
        self._save()

    def recent_conversations(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._data["conversations"][-limit:]

    def search(self, query: str) -> List[Dict[str, Any]]:
        needle = query.lower()
        matches: List[Dict[str, Any]] = []
        for item in self._data["facts"]:
            if needle in item["fact"].lower() or any(needle in tag.lower() for tag in item.get("tags", [])):
                matches.append(item)
        for pair in self._data["conversations"]:
            user = pair["user"].content if isinstance(pair["user"], MemoryRecord) else pair["user"]["content"]
            assistant = pair["assistant"].content if isinstance(pair["assistant"], MemoryRecord) else pair["assistant"]["content"]
            if needle in user.lower() or needle in assistant.lower():
                matches.append({"conversation": pair})
        return matches

    def snapshot(self) -> Dict[str, Any]:
        return self._data
