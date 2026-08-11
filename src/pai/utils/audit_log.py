import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from loguru import logger

class AuditLog:
    """Tamper‑proof audit log using hash chaining."""
    
    def __init__(self, log_path: str = "./data/audit.log"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.last_hash = self._read_last_hash()
    
    def _read_last_hash(self) -> str:
        if not self.log_path.exists():
            return "0" * 64
        with open(self.log_path, "r") as f:
            lines = f.readlines()
            if not lines:
                return "0" * 64
            last_line = lines[-1].strip()
            try:
                return json.loads(last_line)["hash"]
            except:
                return "0" * 64
    
    def log(self, event: str, user_id: str, details: Dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "user_id": user_id,
            "details": details,
            "prev_hash": self.last_hash
        }
        entry_json = json.dumps(entry, sort_keys=True)
        entry["hash"] = hashlib.sha256(entry_json.encode()).hexdigest()
        # Write without the prev_hash field for verification? Keep as is.
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self.last_hash = entry["hash"]
    
    def verify(self) -> bool:
        """Verify the integrity of the whole log chain."""
        if not self.log_path.exists():
            return True
        with open(self.log_path, "r") as f:
            lines = f.readlines()
        prev_hash = "0" * 64
        for line in lines:
            entry = json.loads(line.strip())
            if entry["prev_hash"] != prev_hash:
                return False
            # Recompute hash
            entry_without_hash = {k: v for k, v in entry.items() if k != "hash"}
            recomputed = hashlib.sha256(json.dumps(entry_without_hash, sort_keys=True).encode()).hexdigest()
            if recomputed != entry["hash"]:
                return False
            prev_hash = entry["hash"]
        return True