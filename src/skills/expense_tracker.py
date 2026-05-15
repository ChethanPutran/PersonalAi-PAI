from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "expenses.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_entries() -> List[Dict[str, Any]]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return []


def _save_entries(entries: List[Dict[str, Any]]) -> None:
    DATA_FILE.write_text(json.dumps(entries, indent=2))


def execute(action: str, amount: float = 0.0, category: str = "general", note: str = "") -> Dict[str, Any]:
    """Track simple expenses locally."""
    entries = _load_entries()

    if action == "add":
        entry = {"amount": amount, "category": category, "note": note}
        entries.append(entry)
        _save_entries(entries)
        return {"status": "added", "entry": entry, "count": len(entries)}

    if action == "list":
        return {"status": "ok", "entries": entries}

    if action == "summary":
        total = sum(float(item.get("amount", 0.0)) for item in entries)
        by_category: Dict[str, float] = {}
        for item in entries:
            by_category[item["category"]] = by_category.get(item["category"], 0.0) + float(item.get("amount", 0.0))
        return {"status": "ok", "total": total, "by_category": by_category}

    raise ValueError(f"Unsupported expense action: {action}")