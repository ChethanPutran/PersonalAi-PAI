"""Folder rules for plugins that touch files (the user's per-device settings), enforced on the device.

The backend enforces the same settings; the device does not trust that alone. Pure and small:
``PathPolicy.check`` turns a capability call into ``None`` (fine) or a plain-words refusal.

* Every path-like parameter is expanded (``~``) and resolved with ``realpath`` so a symlink inside an allowed
  folder cannot lead outside it. A path that does not exist yet (a new file) resolves through its existing parent.
* Inside means "the folder itself or something below it": ``/a/Androidx`` is NOT inside ``/a/Android``.
* An empty allowed list means "the plugin's own default roots": nothing is checked here and the plugin's
  environment variable is left unset (the native upper bound then applies). Denied folders and read-only
  are still enforced.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

PATH_PARAMS = ("path", "source", "destination", "outputPath")
POLICY_KEYS = ("allowedPaths", "deniedPaths", "readOnly")
# Mirrors pai.plugins.config_schema.READ_ONLY_BLOCKED (this package imports nothing from `pai`).
READ_ONLY_BLOCKED = ("files.write", "files.delete", "files.move", "files.copy")
# plugin id -> environment variable holding the native upper bound (";"-separated)
ROOTS_ENV = {"files": "PAI_FILES_ROOTS"}


def resolve(raw: str) -> str:
    """Absolute, ``~`` expanded, symlinks resolved."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(raw)))


def is_within(path: str, root: str) -> bool:
    if root == os.sep:
        return True
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def has_policy(values: Optional[Mapping[str, Any]]) -> bool:
    return bool(values) and any(k in values for k in POLICY_KEYS)


@dataclass(frozen=True)
class PathPolicy:
    allowed: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    read_only: bool = False

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> "PathPolicy":
        def strs(key: str) -> List[str]:
            v = values.get(key)
            return [x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []
        return cls(strs("allowedPaths"), strs("deniedPaths"), values.get("readOnly") is True)

    def roots(self) -> List[str]:
        return [resolve(p) for p in self.allowed]

    def roots_env(self) -> Optional[str]:
        """Value for the plugin's roots variable, or None to leave the plugin default."""
        roots = self.roots()
        return ";".join(roots) if roots else None

    def check(self, capability: str, parameters: Optional[Mapping[str, Any]]) -> Optional[str]:
        if self.read_only and capability in READ_ONLY_BLOCKED:
            return "This device is set to read-only for files, so PAI can't change, copy, move or delete them."
        allowed = self.roots()
        denied = [resolve(p) for p in self.denied]
        for key in PATH_PARAMS:
            raw = (parameters or {}).get(key)
            for value in raw if isinstance(raw, list) else [raw]:
                if value is None or value == "":
                    continue
                if not isinstance(value, str) or "\x00" in value:
                    return f"'{key}' is not a valid path."
                real = resolve(value)
                if any(is_within(real, d) for d in denied):
                    return f"'{value}' is in a folder you told PAI never to touch on this device."
                if allowed and not any(is_within(real, a) for a in allowed):
                    return f"'{value}' is outside the folders PAI may use on this device."
        return None


def apply_roots_env(plugin_id: str, policy: Optional[PathPolicy]) -> None:
    """Set (or clear) the native plugin's upper bound so it matches the allowed folders."""
    var = ROOTS_ENV.get(plugin_id)
    if not var:
        return
    value = policy.roots_env() if policy else None
    if value:
        os.environ[var] = value
    else:
        os.environ.pop(var, None)
