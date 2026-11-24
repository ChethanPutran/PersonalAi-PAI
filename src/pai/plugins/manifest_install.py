"""Install-time manifest validation.

Two independent layers, both must pass:

  1. JSON Schema (Draft 2020-12) — catches structural typos with
     industry-standard error messages ("device_type" vs "deviceTypes",
     missing `runtime.kind`, empty `id`, …).

  2. Pydantic v2 — builds the typed model, applies semantic checks
     (kind↔deviceTypes consistency, duplicate capability ids,
      required ⊆ properties, …).

`load_manifest_file()` runs both and returns a fully typed model,
or raises `ManifestError` with all problems at once.

`load_manifest_directory()` discovers only files named `manifest.json`
(skipping VCS / build / cache folders) so it never trips over
`package.json`, `tsconfig.json`, `build/output.json`, etc.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from pai.plugins.manifest import PluginManifest
from pai.plugins.manifest_models import CapabilityParameter



_SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")
_SCHEMA: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)


# ---------------------------------------------------------------------------
# Directory-scan configuration
# ---------------------------------------------------------------------------

#: Only files with this name are considered plugin manifests.
MANIFEST_FILENAME = "manifest.json"

#: Folder names that are never descended into when scanning for manifests.
EXCLUDED_DIRS: frozenset[str] = frozenset({
    # VCS
    ".git", ".hg", ".svn",
    # Python
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "venv", "env", ".tox", ".nox", "site-packages",
    # JS / TS
    "node_modules", "dist", "build", ".next", ".nuxt", ".cache",
    # JVM / Kotlin / Android
    "target", "out", ".gradle", ".idea", ".kotlin", "build",
    # Rust / Go
    "target", "vendor",
    # Editors
    ".vscode",
})


class ManifestError(ValueError):
    """Raised when a manifest fails install-time validation."""

    def __init__(
        self,
        message: str,
        *,
        schema_errors: list[jsonschema.ValidationError] | None = None,
        model_error: ValidationError | None = None,
    ) -> None:
        super().__init__(message)
        self.schema_errors = schema_errors or []
        self.model_error = model_error


# ---------------------------------------------------------------------------
# Layer 1: JSON Schema
# ---------------------------------------------------------------------------

def validate_dict(raw: dict[str, Any]) -> None:
    """Raise ManifestError if `raw` violates the JSON Schema."""
    errors = sorted(_VALIDATOR.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    lines: list[str] = []
    for e in errors:
        loc = ".".join(str(p) for p in e.absolute_path) or "<root>"
        lines.append(f"  {loc}: {e.message}")
    raise ManifestError(
        f"manifest failed JSON-schema validation with {len(errors)} error(s):\n"
        + "\n".join(lines),
        schema_errors=errors,
    )


# ---------------------------------------------------------------------------
# Layer 2: Pydantic
# ---------------------------------------------------------------------------

def build_model(raw: dict[str, Any]) -> PluginManifest:
    """Construct the typed model. Assumes `raw` already passed JSON Schema."""
    try:
        return PluginManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(
            f"manifest failed semantic validation:\n{exc}",
            model_error=exc,
        ) from exc

def lint_manifest(m: PluginManifest) -> list[str]:
    """Non-fatal style/quality suggestions. Returns human-readable warnings."""
    warnings: list[str] = []

    def _walk(name: str, p: CapabilityParameter) -> None:
        if p.type == "array" and p.items is None:
            warnings.append(
                f"capability {name!r}: array parameter {p.type!r} has no "
                f"'items' schema — LLM callers will not know the element shape"
            )
        if p.type == "object" and not p.properties:
            warnings.append(
                f"capability {name!r}: object parameter has no 'properties' "
                f"— callers will guess the shape"
            )
        for child_name, child in (p.properties or {}).items():
            _walk(f"{name}.{child_name}", child)
        if p.items is not None:
            _walk(f"{name}[]", p.items)

    for cap in m.capabilities:
        for pname, p in cap.parameters.properties.items():
            _walk(f"{cap.id}.{pname}", p)

    return warnings

# ---------------------------------------------------------------------------
# Convenience: dict / file / directory
# ---------------------------------------------------------------------------

def validate_manifest(raw: dict[str, Any]) -> PluginManifest:
    validate_dict(raw)
    return build_model(raw)


def load_manifest_file(path: str | Path) -> PluginManifest:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{p}: invalid JSON: {exc}") from exc
    try:
        return validate_manifest(raw)
    except ManifestError as exc:
        raise ManifestError(f"{p}: {exc}") from exc


def _is_excluded(path: Path, root: Path, excluded: frozenset[str]) -> bool:
    """True if any directory between `root` and `path` is in `excluded`."""
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    # last part is the filename — only inspect intermediate directories
    return any(part in excluded for part in rel_parts[:-1])


def load_manifest_directory(
    directory: str | Path,
    *,
    recursive: bool = True,
    manifest_filename: str = MANIFEST_FILENAME,
    excluded_dirs: frozenset[str] = EXCLUDED_DIRS,
) -> dict[str, PluginManifest]:
    """Validate every `manifest.json` under `directory`.

    Only files named exactly `manifest_filename` are picked up, and any
    directory whose name is in `excluded_dirs` is skipped (git, build
    outputs, virtualenvs, node_modules, …).

    Returns a `{path_str: manifest}` mapping. Raises `ManifestError`
    listing every failure if any manifest failed to validate.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ManifestError(f"{root}: not a directory")

    pattern = f"**/{manifest_filename}" if recursive else manifest_filename

    result: dict[str, PluginManifest] = {}
    failures: list[str] = []

    for path in sorted(root.glob(pattern)):
        if _is_excluded(path, root, excluded_dirs):
            continue
        try:
            result[str(path)] = load_manifest_file(path)
        except ManifestError as exc:
            failures.append(str(exc))

    if failures:
        raise ManifestError(
            f"{len(failures)} manifest(s) failed validation:\n\n"
            + "\n\n".join(failures),
        )
    return result


# ---------------------------------------------------------------------------
# CLI: `python -m pai.plugins.manifest_install <path>...`
# ---------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    import sys
    paths = [Path(a) for a in argv]
    if not paths:
        print(
            "usage: pai-validate-manifests <file-or-dir>...",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for p in paths:
        try:
            if p.is_dir():
                manifests = load_manifest_directory(p)
                if not manifests:
                    print(f"WARN {p}: no {MANIFEST_FILENAME} files found",
                          file=sys.stderr)
                for path_str, m in manifests.items():
                    print(f"OK  {path_str}: {m.id}@{m.version} "
                          f"({m.type}, {m.runtime.kind})")
            else:
                m = load_manifest_file(p)
                print(f"OK  {p}: {m.id}@{m.version} "
                      f"({m.type}, {m.runtime.kind})")
                for w in lint_manifest(m):
                    print(f"    WARN {w}", file=sys.stderr)
        except ManifestError as exc:
            failures += 1
            print(f"ERR {p}:\n{exc}\n", file=sys.stderr)

    return 1 if failures else 0


def main() -> int:
    """Console-script entry point (`pai-validate-manifests`)."""
    import sys
    return _main(sys.argv[1:])

if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))