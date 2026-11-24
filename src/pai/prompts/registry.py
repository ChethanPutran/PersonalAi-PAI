"""File-based prompt template registry with versioning."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2.exceptions import UndefinedError
from pai.config.models.prompts import PromptRegistryConfig
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: str
    source: str
    sha256: str
    meta: Dict[str, Any]
    jinja_template: Any = field(repr=False)

    def render(self, **variables: Any) -> str:
        try:
            return self.jinja_template.render(**variables)
        except UndefinedError as exc:
            raise ValueError(
                f"Prompt {self.id}@{self.version}: missing variable — {exc}. "
                f"Declared variables: {sorted(self.meta.get('variables', {}))}"
            ) from exc


class PromptRegistry:
    """Loads Jinja2 templates from `<root>/<path>/_meta.yaml` + version files.

    Version resolution:
      1. Explicit `version=` argument
      2. `current:` field in `_meta.yaml`
      3. Fail fast — never silently pick a wrong version

    In `frozen` mode (production), templates are loaded once at construction
    and edits on disk are ignored until restart. In dev mode, edits are
    picked up on the next `get()` — no restart needed.
    """

    def __init__(
        self,
        config: PromptRegistryConfig,
    ) -> None:
        self.root = Path(config.root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Prompt root not found: {self.root}")

        self.frozen = config.frozen
        self.override_versions = config.override_versions or {}

        self._jinja = Environment(
            loader=FileSystemLoader(str(self.root)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._cache: Dict[str, PromptTemplate] = {}

        logger.info(
            "PromptRegistry initialized root=%s frozen=%s overrides=%s",
            self.root, self.frozen, self.override_versions or {},
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get(
        self,
        prompt_id: str,
        version: Optional[str] = None,
    ) -> PromptTemplate:
        # CLI/env override beats everything — useful for A/B testing
        effective_version = (
            version
            or self.override_versions.get(prompt_id)
            or None
        )
        cache_key = f"{prompt_id}@{effective_version or 'auto'}"

        if self.frozen and cache_key in self._cache:
            return self._cache[cache_key]

        meta = self._load_meta(prompt_id)
        resolved = effective_version or meta["current"]

        if resolved not in meta.get("versions", {}):
            raise KeyError(
                f"{prompt_id}: version {resolved!r} not declared in "
                f"_meta.yaml. Available: {sorted(meta.get('versions', {}))}"
            )

        template_path = (
            self.root / prompt_id.replace(".", "/") / f"{resolved}.j2"
        )
        if not template_path.is_file():
            raise FileNotFoundError(
                f"{prompt_id}: template file missing at {template_path}"
            )

        source = template_path.read_text(encoding="utf-8")
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]

        template = PromptTemplate(
            id=prompt_id,
            version=resolved,
            source=source,
            sha256=sha,
            meta=meta["versions"][resolved],
            jinja_template=self._jinja.from_string(source),
        )

        # Content-hash check: catches in-place edits that didn't bump
        # `current:` or add a new version file. Loud warning, not fatal.
        prior = self._cache.get(cache_key)
        if prior and prior.sha256 != sha:
            logger.warning(
                "Prompt %s@%s changed on disk (sha %s → %s) without a "
                "version bump. Bump _meta.yaml for reproducibility.",
                prompt_id, resolved, prior.sha256, sha,
            )

        self._cache[cache_key] = template
        return template

    def list_versions(self, prompt_id: str) -> Dict[str, Dict[str, Any]]:
        meta = self._load_meta(prompt_id)
        return meta.get("versions", {})

    def current_version(self, prompt_id: str) -> str:
        return self._load_meta(prompt_id)["current"]

    # ------------------------------------------------------------------

    def _load_meta(self, prompt_id: str) -> Dict[str, Any]:
        meta_path = self.root / prompt_id.replace(".", "/") / "_meta.yaml"
        if not meta_path.is_file():
            raise FileNotFoundError(
                f"No _meta.yaml for prompt {prompt_id!r} at {meta_path}"
            )
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "current" not in data:
            raise ValueError(f"{meta_path}: missing 'current' field")
        return data