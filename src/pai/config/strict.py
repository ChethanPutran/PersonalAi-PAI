"""Reject settings the models do not have.

`Config` forbids unknown TOP-LEVEL keys, but every nested model ignores unknown keys, so a typo such as
`orchestration.task_timeout` (the field is `task_timeout_seconds`) was accepted and silently did nothing. This walks the
YAML data against the models and reports every key that matches no field (or alias), with a "did you mean" hint.
Maps with arbitrary keys (llm_routes, llm_pricing, executor.executors ...) are not checked below their own name.

CLI: `pai-validate-config [files]` checks config.yaml (+ overrides.yaml) and models.yaml. No server, database or secrets
needed (placeholder secrets are used for the validation itself).
"""
from __future__ import annotations

import difflib
import functools
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from pydantic import AliasChoices, AliasPath, BaseModel

from pai.config import settings_admin as S

#: the subtree filled from .env, never from YAML
_SKIP_ROOTS = ("secrets",)


def _all_models() -> Dict[str, List[type]]:
    out: Dict[str, List[type]] = {}
    stack = list(BaseModel.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        out.setdefault(cls.__name__, []).append(cls)
    return out


def _alias_keys(classes: Sequence[type]) -> Set[str]:
    keys: Set[str] = set()
    for cls in classes:
        for name, field in getattr(cls, "model_fields", {}).items():
            keys.add(name)
            for alias in (field.alias, field.validation_alias):
                if isinstance(alias, str):
                    keys.add(alias)
                elif isinstance(alias, AliasChoices):
                    keys.update(c for c in alias.choices if isinstance(c, str))
                elif isinstance(alias, AliasPath) and alias.path and isinstance(alias.path[0], str):
                    keys.add(alias.path[0])
    return keys


@functools.lru_cache(maxsize=4)
def _schema_for(config_cls: type) -> Tuple[Dict[str, Any], Dict[str, Set[str]], Set[str]]:
    schema = config_cls.model_json_schema()
    by_name = _all_models()
    aliases = {name: _alias_keys(classes) for name, classes in by_name.items()}
    return schema, aliases, S.freeform_paths(schema)


def unknown_keys(data: Mapping[str, Any], config_cls: type) -> List[Tuple[str, Optional[str]]]:
    """[(dotted path, suggested field or None)] for every key in `data` that matches no model field or alias."""
    schema, aliases, freeform = _schema_for(config_cls)
    defs = schema.get("$defs", {})

    def resolve(node: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Optional[str]]:
        name = None
        while "$ref" in node:
            name = node["$ref"].split("/")[-1]
            node = defs[name]
        for key in ("anyOf", "oneOf"):
            for alt in node.get(key, []):
                r, n = resolve(alt)
                if r.get("properties"):
                    return r, n
        return node, name

    out: List[Tuple[str, Optional[str]]] = []

    def walk(values: Mapping[str, Any], node: Mapping[str, Any], prefix: str, def_name: Optional[str]) -> None:
        props = node.get("properties") or {}
        accepted = set(props) | aliases.get(def_name or "", set())
        for key, value in values.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if not prefix and key in _SKIP_ROOTS:
                continue
            if path in freeform:
                continue
            if key not in accepted:
                close = difflib.get_close_matches(str(key), sorted(props), n=1, cutoff=0.6)
                out.append((path, close[0] if close else None))
                continue
            if isinstance(value, Mapping) and key in props:
                child, child_name = resolve(props[key])
                if child.get("properties"):
                    walk(value, child, path, child_name)

    root, _ = resolve(schema)
    walk(data, schema, "", config_cls.__name__)
    return out


def canonicalize(data: Mapping[str, Any], config_cls: type) -> Dict[str, Any]:
    """Rename aliased keys to their field names (`llm_provider.model` -> `llm_provider.model_name`), so config.yaml,
    overrides.yaml and the admin panel all talk about a setting by the same path. Unknown keys are left alone."""
    schema, _aliases, freeform = _schema_for(config_cls)
    defs = schema.get("$defs", {})
    by_name = _all_models()

    def resolve(node: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Optional[str]]:
        name = None
        while "$ref" in node:
            name = node["$ref"].split("/")[-1]
            node = defs[name]
        for key in ("anyOf", "oneOf"):
            for alt in node.get(key, []):
                r, n = resolve(alt)
                if r.get("properties"):
                    return r, n
        return node, name

    def alias_to_field(def_name: Optional[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for cls in by_name.get(def_name or "", []):
            for fname, field in getattr(cls, "model_fields", {}).items():
                for alias in (field.alias, field.validation_alias):
                    if isinstance(alias, str):
                        out[alias] = fname
                    elif isinstance(alias, AliasChoices):
                        out.update({c: fname for c in alias.choices if isinstance(c, str)})
        return out

    def walk(values: Mapping[str, Any], node: Mapping[str, Any], def_name: Optional[str], prefix: str) -> Dict[str, Any]:
        props = node.get("properties") or {}
        a2f = alias_to_field(def_name)
        out: Dict[str, Any] = {}
        for key, value in values.items():
            name = key if key in props else a2f.get(key, key)
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(value, Mapping) and name in props and path not in freeform:
                child, child_name = resolve(props[name])
                if child.get("properties"):
                    value = walk(value, child, child_name, path)
            if name in out and name != key and isinstance(out[name], Mapping) and isinstance(value, Mapping):
                value = S.deep_merge(out[name], value)
            elif name in out and name != key:
                continue                                         # the canonical key was given explicitly: it wins over its alias
            out[name] = value
        return out

    return walk(data, schema, config_cls.__name__, "")


def format_unknown(found: Sequence[Tuple[str, Optional[str]]]) -> str:
    return "; ".join(f"'{p}'" + (f" (did you mean '{s}'?)" if s else " (no such setting)") for p, s in found)


# ───────────────────────────────────────────────────────────── command line

def _placeholder_secrets(config_cls: type) -> None:
    """Config needs the required secrets to exist; validating the YAML must not (CI has no .env)."""
    secrets_field = config_cls.model_fields.get("secrets")
    for f in ("SECURITY__JWT_SECRET", "SECURITY__ENCRYPTION_KEY"):
        os.environ.setdefault(f, "validate-only-placeholder")


def validate_files(paths: Sequence[Path], models_file: Optional[Path] = None) -> int:
    """Validate config YAML files (the later ones override the earlier ones) and the model registry."""
    from pai.config.app import Config
    from pai.config.model_registry import ModelRegistryError, load_registry
    _placeholder_secrets(Config)
    problems = 0
    merged: Dict[str, Any] = {}
    for p in paths:
        try:
            data = S.load_yaml_dict(p)
        except S.SettingsError as exc:
            print(f"ERR {p}: {exc}")
            problems += 1
            continue
        if not p.is_file():
            print(f"-   {p}: not present (skipped)")
            continue
        bad = unknown_keys(data, Config)
        if bad:
            problems += len(bad)
            print(f"ERR {p}: {len(bad)} setting(s) the models do not have (they would be silently ignored):")
            for path, sug in bad:
                print(f"      {path}" + (f"   -> did you mean '{sug}'?" if sug else "   (no such setting)"))
        else:
            print(f"OK  {p}: every key is a real setting")
        merged = S.deep_merge(merged, data)
    if merged:
        errors = S.validate_settings(Config, {}, merged)
        if errors:
            problems += len(errors)
            print(f"ERR the merged configuration is invalid ({len(errors)} problem(s)):")
            for e in errors:
                print(f"      {e['path']}: {e['message']}")
        else:
            print("OK  the merged configuration validates as a Config")
    if models_file is not None:
        try:
            reg = load_registry(models_file)
            if not models_file.is_file():
                print(f"-   {models_file}: not present (skipped)")
            else:
                print(f"OK  {models_file}: {len(reg.llm)} language, {len(reg.stt)} stt, {len(reg.tts)} tts model(s)")
                for w in reg.warnings:
                    print(f"WARN {models_file}: {w}")
        except ModelRegistryError as exc:
            problems += 1
            print(f"ERR {exc}")
    return 1 if problems else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`pai-validate-config [config.yaml ...]`: unknown/typo'd keys, invalid values, and the model registry."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print(main.__doc__)
        return 0
    paths = [Path(a) for a in args] or [Path(S.BASE_FILE), Path(S.OVERRIDES_FILE)]
    models = None if args else Path("config/models.yaml")
    return validate_files(paths, models)


if __name__ == "__main__":
    raise SystemExit(main())
