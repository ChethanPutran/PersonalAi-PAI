import dataclasses
import json
from datetime import datetime
from typing import Any, Dict


def _json_default(obj: Any) -> Any:
    """Fallback for json.dumps on non-primitive types."""
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {
            k: v for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return str(obj)

def _matches_where(
    metadata: Dict[str, Any],
    where: Dict[str, Any],
) -> bool:
    """
    Minimal Chroma-style where-clause evaluator for the in-memory
    fallback path.

    Supports the operators the codebase actually uses:
      - plain equality:  {"user_id": "alice"}
      - $eq / $ne / $in / $nin:  {"user_id": {"$eq": "alice"}}
      - nested field access is not supported; metadata is flat

    Chroma's full grammar (compound $and/$or, numeric comparisons) is
    not implemented here. If the fallback is ever used in production,
    extend this to match. For local dev — the only place the fallback
    runs — the subset above is sufficient.
    """
    for key, cond in where.items():
        value = metadata.get(key)
        if isinstance(cond, dict):
            if "$eq" in cond and value != cond["$eq"]:
                return False
            if "$ne" in cond and value == cond["$ne"]:
                return False
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$nin" in cond and value in cond["$nin"]:
                return False
        else:
            if value != cond:
                return False
    return True