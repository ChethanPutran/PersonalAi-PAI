"""Per-plugin effective settings, kept under the agent's state dir so enforcement works offline.

One JSON file per plugin (``<state_dir>/plugin_config/<plugin_id>.json``), written atomically, mode 0600.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

logger = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class PluginConfigStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    def _file(self, plugin_id: str) -> Path:
        if not _SAFE_ID.match(plugin_id) or plugin_id in (".", ".."):
            raise ValueError(f"bad plugin id {plugin_id!r}")
        return self.dir / f"{plugin_id}.json"

    def save(self, plugin_id: str, values: Mapping[str, Any]) -> None:
        target = self._file(plugin_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(dict(values), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, plugin_id: str) -> None:
        self._file(plugin_id).unlink(missing_ok=True)

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not self.dir.is_dir():
            return out
        for f in self.dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict):
                    out[f.stem] = data
            except Exception:
                logger.warning("Ignoring unreadable plugin settings %s", f)
        return out
