"""Edit the configuration from the admin panel, safely.

* Saved values go to `config/overrides.yaml`, which is merged OVER `config/config.yaml` at startup, so config.yaml (and
  its comments) is never rewritten and "reset" just removes a key from the overrides.
* Every change is validated against the real `Config` model BEFORE it is written (unknown keys are refused, types and
  ranges are checked by the same code that will load it), and the file is replaced atomically with a `.bak` copy.
* Secrets (API keys, passwords, the JWT secret ...) are never shown or written here: they live in `.env`.
* Changes apply on the next start. `pending()` lists the saved values that differ from what is running now.
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml
from pydantic import ValidationError

#: Where admin edits are saved (relative to the working directory, like config.yaml). Merged over config.yaml.
OVERRIDES_FILE = "config/overrides.yaml"
BASE_FILE = "config/config.yaml"

_SECRET_NAME = re.compile(r"(?:^|_)(?:password|secret|token|api_key|private_key)$|_key$", re.I)
#: whole subtrees that are never editable here
_HIDDEN_ROOTS = ("secrets",)


class SettingsError(ValueError):
    """A change was refused. `errors` is a list of {path, message}."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, str]]] = None, status: int = 422) -> None:
        super().__init__(message)
        self.errors = errors or [{"path": "", "message": message}]
        self.status = status


# ───────────────────────────────────────────────────────────── paths and dicts

def get_path(data: Mapping[str, Any], path: str, default: Any = None) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[parts[-1]] = value


def del_path(data: Dict[str, Any], path: str) -> bool:
    """Remove a key and prune the empty parents it leaves behind. Returns whether something was removed."""
    parts = path.split(".")
    chain: List[Tuple[Dict[str, Any], str]] = []
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        chain.append((node, part))
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False
    del node[parts[-1]]
    for parent, key in reversed(chain):
        if parent[key] == {}:
            del parent[key]
        else:
            break
    return True


def deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> Dict[str, Any]:
    """`over` wins; dicts merge recursively, everything else (lists included) is replaced."""
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def flatten(data: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    """{"a": {"b": 1}, "c": [1]} -> {"a.b": 1, "c": [1]} (lists and empty dicts are leaves)."""
    out: Dict[str, Any] = {}
    for k, v in data.items():
        p = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, Mapping) and v:
            out.update(flatten(v, p))
        else:
            out[p] = v
    return out


# ───────────────────────────────────────────────────────────── secrets

def is_secret_path(path: str) -> bool:
    """True for anything that must stay in .env: the `secrets` subtree and string settings named like a credential."""
    parts = path.split(".")
    if parts[0] in _HIDDEN_ROOTS:
        return True
    return any(_SECRET_NAME.search(p) for p in parts)


def strip_secrets(data: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy without secret values (a secret that is set shows up only in `secret_paths`)."""
    def walk(node: Mapping[str, Any], prefix: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if is_secret_path(p):
                continue
            out[k] = walk(v, p) if isinstance(v, Mapping) else v
        return out
    return walk(data, "")


def secret_paths(schema: Mapping[str, Any]) -> List[str]:
    """Every secret setting the schema knows about (so the UI can show them locked)."""
    defs = schema.get("$defs", {})
    found: List[str] = []

    def resolve(node: Mapping[str, Any]) -> Mapping[str, Any]:
        while "$ref" in node:
            node = defs[node["$ref"].split("/")[-1]]
        for key in ("anyOf", "oneOf"):
            for alt in node.get(key, []):
                r = resolve(alt)
                if r.get("properties"):
                    return r
        return node

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for name, child in (resolve(node).get("properties") or {}).items():
            p = f"{prefix}.{name}" if prefix else name
            if is_secret_path(p):
                found.append(p)
            else:
                walk(child, p)

    walk(schema, "")
    return found


# ───────────────────────────────────────────────────────────── files

def load_yaml_dict(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SettingsError(f"{p}: cannot read: {exc}", status=500) from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{p}: the top level must be a mapping", status=500)
    return data


HEADER = ("# Settings saved from the admin panel. Merged OVER config/config.yaml at startup (restart to apply).\n"
          "# Edit in the admin panel (Settings), or delete a key here to fall back to config.yaml.\n")


def write_overrides(path: str | Path, data: Mapping[str, Any]) -> None:
    """Atomically replace the overrides file (keeping the previous one as `.bak`); an empty set removes the file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        shutil.copyfile(p, p.with_suffix(p.suffix + ".bak"))
    if not data:
        p.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(HEADER)
            yaml.safe_dump(dict(data), f, sort_keys=False, allow_unicode=True, default_flow_style=False)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ───────────────────────────────────────────────────────────── validation

def _canonical(config_cls: Any, data: Mapping[str, Any]) -> Dict[str, Any]:
    from pai.config.strict import canonicalize
    return canonicalize(data, config_cls)


def validate_settings(config_cls: Any, base: Mapping[str, Any], overrides: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Validate base+overrides with the real Config model. Returns [{path, message}] (empty = valid)."""
    try:
        config_cls.model_validate(deep_merge(_canonical(config_cls, base), _canonical(config_cls, overrides)))
    except ValidationError as exc:
        return [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return []


def apply_changes(config_cls: Any, schema: Mapping[str, Any], base: Mapping[str, Any], overrides: Mapping[str, Any],
                  set_values: Mapping[str, Any], reset: Iterable[str] = ()) -> Dict[str, Any]:
    """The new overrides after `set_values` and `reset`, or SettingsError (nothing is written here)."""
    known = known_paths(schema)
    freeform = freeform_paths(schema)
    errors: List[Dict[str, str]] = []
    new = copy.deepcopy(dict(overrides))
    for path in reset:
        if path.split(".")[0] in _HIDDEN_ROOTS or is_secret_path(path):
            errors.append({"path": path, "message": "secret settings live in .env and are not managed here"})
        else:
            del_path(new, path)
    for path, value in set_values.items():
        if is_secret_path(path):
            errors.append({"path": path, "message": "secret settings live in .env and cannot be changed here"})
        elif path not in known:
            errors.append({"path": path, "message": "unknown setting"})
        elif _unknown_inside(value, path, known, freeform):
            errors.extend({"path": p, "message": "unknown setting"} for p in _unknown_inside(value, path, known, freeform))
        elif isinstance(value, Mapping) and any(is_secret_path(p) for p in flatten(value, path)):
            errors.append({"path": path, "message": "contains a secret setting: secrets live in .env and cannot be changed here"})
        else:
            set_path(new, path, value)
    if errors:
        raise SettingsError(f"{len(errors)} setting(s) refused", errors, status=422)
    problems = validate_settings(config_cls, base, new)
    if problems:
        raise SettingsError(f"{len(problems)} invalid value(s): nothing was saved", problems, status=422)
    return new


def freeform_paths(schema: Mapping[str, Any]) -> set[str]:
    """Settings that are maps with arbitrary keys (llm_routes, llm_pricing ...): keys below them are not checked."""
    defs = schema.get("$defs", {})
    out: set[str] = set()

    def resolve(node: Mapping[str, Any]) -> Mapping[str, Any]:
        while "$ref" in node:
            node = defs[node["$ref"].split("/")[-1]]
        for key in ("anyOf", "oneOf"):
            for alt in node.get(key, []):
                r = resolve(alt)
                if r.get("properties") or r.get("additionalProperties"):
                    return r
        return node

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for name, child in (resolve(node).get("properties") or {}).items():
            p = f"{prefix}.{name}" if prefix else name
            r = resolve(child)
            if r.get("properties"):
                walk(child, p)
            elif r.get("type") == "object" or r.get("additionalProperties"):
                out.add(p)

    walk(schema, "")
    return out


def _unknown_inside(value: Any, path: str, known: set[str], freeform: set[str]) -> List[str]:
    """Paths inside a dict value that the schema does not know (and that are not under a free-form map)."""
    if not isinstance(value, Mapping):
        return []
    bad: List[str] = []
    for sub_path in flatten(value, path):
        parts = sub_path.split(".")
        under_freeform = any(".".join(parts[:i]) in freeform for i in range(1, len(parts) + 1))
        if sub_path not in known and not under_freeform and not any(k.startswith(sub_path + ".") for k in known):
            bad.append(sub_path)
    return bad


def known_paths(schema: Mapping[str, Any]) -> set[str]:
    """Paths that can be set: every leaf and every free-form object (dict-valued setting) in the schema."""
    defs = schema.get("$defs", {})
    out: set[str] = set()

    def resolve(node: Mapping[str, Any]) -> Mapping[str, Any]:
        while "$ref" in node:
            node = defs[node["$ref"].split("/")[-1]]
        for key in ("anyOf", "oneOf"):
            alts = [resolve(a) for a in node.get(key, [])]
            objs = [a for a in alts if a.get("properties")]
            if objs:
                return objs[0]
        return node

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        props = resolve(node).get("properties")
        if not props:
            return
        for name, child in props.items():
            p = f"{prefix}.{name}" if prefix else name
            out.add(p)                      # a whole nested object may be replaced too (its fields are validated)
            walk(child, p)

    walk(schema, "")
    return {p for p in out if p.split(".")[0] not in _HIDDEN_ROOTS}


def pending(overrides: Mapping[str, Any], running: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Saved override values that differ from what the server is running with (they apply on restart)."""
    out = []
    for path, saved in flatten(overrides).items():
        now = get_path(running, path, None)
        if now != saved:
            out.append({"path": path, "saved": saved, "running": now})
    return out


def snapshot(config_cls: Any, running: Mapping[str, Any], base: Mapping[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Everything the Settings page needs. Secret values are never included."""
    schema = config_cls.model_json_schema()
    schema = {**schema, "properties": {k: v for k, v in schema.get("properties", {}).items() if k not in _HIDDEN_ROOTS}}
    return {
        "schema": schema,
        "values": strip_secrets(running),                  # what the server is running with now
        "base": strip_secrets(_canonical(config_cls, base)),             # what config/config.yaml says (field names, not aliases)
        "overrides": strip_secrets(_canonical(config_cls, overrides)),   # what the admin panel saved on top
        "secret_paths": secret_paths(schema),
        "pending": [p for p in pending(strip_secrets(overrides), strip_secrets(running))],
        "overrides_file": OVERRIDES_FILE,
    }
