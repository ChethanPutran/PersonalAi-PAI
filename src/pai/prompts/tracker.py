"""Prompt usage tracking — span attributes + optional DB persistence."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pai.prompts.registry import PromptTemplate

logger = logging.getLogger(__name__)


class PromptVersionTracker:
    """Records which prompt version was used for each render.

    Two sinks:
      • span attributes (always — free, flows to your trace store)
      • DB row in `prompt_renders` (opt-in — enables queries like
        "how often was v1.0.0 used vs v1.1.0 this week?")

    If persistence fails, the render still succeeds — tracking must
    never break execution.
    """

    def __init__(
        self,
        *,
        session_factory=None,       # async_sessionmaker or None
        enabled: bool = True,
    ) -> None:
        self.session_factory = session_factory
        self.enabled = enabled

    async def record(
        self,
        *,
        template: PromptTemplate,
        rendered_text: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        span=None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        # 1. Span attributes — always
        if span is not None:
            try:
                span.set_attribute("prompt.id", template.id)
                span.set_attribute("prompt.version", template.version)
                span.set_attribute("prompt.sha256", template.sha256)
                span.set_attribute(
                    "prompt.rendered_length", len(rendered_text),
                )
            except Exception:
                logger.debug(
                    "PromptVersionTracker: span attribute write failed",
                    exc_info=True,
                )

        # 2. DB persistence — optional
        if not self.enabled or self.session_factory is None:
            return

        try:
            from pai.storage.models import PromptRenderModel
            async with self.session_factory() as db:
                db.add(PromptRenderModel(
                    prompt_id=template.id,
                    prompt_version=template.version,
                    sha256=template.sha256,
                    user_id=user_id,
                    session_id=session_id,
                    rendered_at=datetime.now(timezone.utc),
                    rendered_length=len(rendered_text),
                    extra=extra or {},
                ))
                await db.commit()
        except Exception:
            logger.warning(
                "PromptVersionTracker: DB record failed for %s@%s",
                template.id, template.version,
                exc_info=True,
            )