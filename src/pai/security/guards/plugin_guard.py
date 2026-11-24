"""Centralized policy enforcement for plugin invocations.

Two independent layers:

  Plugin side (C++)   enforces the hard limits — command whitelist,
                      path scoping. If the plugin is called with a bad
                      input, it returns an error. No Python involvement.

  Guard side (Python) enforces the policy layer — confirmation gates,
                      trusted-source checks, audit logging. Runs before
                      the plugin is invoked; can short-circuit.

The Python guard is defense-in-depth: even if a plugin has a bug, the
guard blocks obviously-abusive inputs at a higher level.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pai.storage.models import PluginAuditModel, TrustedDeviceModel


logger = logging.getLogger(__name__)


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class GuardResult:
    decision: Decision
    reason: Optional[str] = None
    # Populated when decision=CONFIRM
    confirmation_payload: Optional[Dict[str, Any]] = None
    # Populated when decision=DENY
    denial_code: Optional[str] = None


class PluginGuard:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        plugin_manager,
        device_manager,
    ):
        self.session_factory = session_factory
        self.plugin_manager = plugin_manager
        self.device_manager = device_manager

    # ------------------------------------------------------------------
    # Trusted devices
    # ------------------------------------------------------------------

    async def is_device_trusted(self, user_id: str, device_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrustedDeviceModel).where(
                    TrustedDeviceModel.user_id == user_id,
                    TrustedDeviceModel.device_id == device_id,
                ).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def trust_device(self, user_id: str, device_id: str) -> None:
        async with self.session_factory() as session:
            existing = await session.execute(
                select(TrustedDeviceModel).where(
                    TrustedDeviceModel.user_id == user_id,
                    TrustedDeviceModel.device_id == device_id,
                ).limit(1)
            )
            if existing.scalar_one_or_none() is None:
                session.add(TrustedDeviceModel(
                    user_id=user_id, device_id=device_id,
                ))
                await session.commit()

    async def untrust_device(self, user_id: str, device_id: str) -> None:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TrustedDeviceModel).where(
                    TrustedDeviceModel.user_id == user_id,
                    TrustedDeviceModel.device_id == device_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    # ------------------------------------------------------------------
    # The main check
    # ------------------------------------------------------------------

    async def check(
        self,
        *,
        user_id: str,
        device_id: str,
        source_device_id: Optional[str],
        capability: str,
        parameters: Dict[str, Any],
        task_id: Optional[str] = None,
    ) -> GuardResult:
        """Evaluate the invocation against all policies.

        Order matters — cheapest checks first, and DENY short-circuits
        before CONFIRM (no point asking the user to approve a request
        that would be denied anyway).
        """
        catalog_entry = self.plugin_manager.lookup_capability(capability)
        if catalog_entry is None:
            return GuardResult(
                decision=Decision.DENY,
                reason=f"capability '{capability}' not in catalog",
                denial_code="not_in_catalog",
            )

        metadata = catalog_entry.get("metadata") or {}

        # ---- 1. Trusted-source check (for the highest-risk capabilities) ----
        if metadata.get("requires_trusted_source", False):
            if not source_device_id:
                return await self._deny(
                    user_id, device_id, source_device_id, capability,
                    parameters, task_id,
                    "no source_device_id supplied",
                    "no_source_device",
                )
            trusted = await self.is_device_trusted(user_id, source_device_id)
            if not trusted:
                return await self._deny(
                    user_id, device_id, source_device_id, capability,
                    parameters, task_id,
                    f"device {source_device_id} is not trusted by user {user_id}",
                    "untrusted_source",
                )

        # ---- 2. Command whitelist (belt-and-suspenders — plugin enforces too) ----
        if capability == "shell.exec":
            allowed_prefixes = metadata.get("allowed_prefixes") or []
            command = str(parameters.get("command", ""))
            if not _command_allowed(command, allowed_prefixes):
                return await self._deny(
                    user_id, device_id, source_device_id, capability,
                    parameters, task_id,
                    f"command '{command[:80]}' not whitelisted",
                    "command_not_whitelisted",
                )

        # ---- 3. Path scoping (belt-and-suspenders) ----
        if capability.startswith("files."):
            allowed_roots = metadata.get("allowed_roots") or []
            paths = _extract_paths(capability, parameters)
            for p in paths:
                if not _path_in_roots(p, allowed_roots):
                    return await self._deny(
                        user_id, device_id, source_device_id, capability,
                        parameters, task_id,
                        f"path '{p}' outside allowed roots",
                        "path_outside_roots",
                    )

        # ---- 4. Confirmation gate ----
        if metadata.get("requires_confirmation", False):
            await self._audit(
                user_id, device_id, source_device_id, task_id,
                capability, parameters, "awaiting_confirmation",
                "user must approve",
            )
            return GuardResult(
                decision=Decision.CONFIRM,
                reason=f"'{capability}' requires user confirmation",
                confirmation_payload={
                    "capability": capability,
                    "parameters": parameters,
                    "task_id": task_id,
                },
            )

        # ---- 5. Allowed ----
        await self._audit(
            user_id, device_id, source_device_id, task_id,
            capability, parameters, "allowed", None,
        )
        return GuardResult(decision=Decision.ALLOW)

    async def _deny(
        self, user_id, device_id, source_device_id, capability,
        parameters, task_id, reason, code,
    ) -> GuardResult:
        await self._audit(
            user_id, device_id, source_device_id, task_id,
            capability, parameters, "denied", reason,
        )
        logger.warning(
            "PluginGuard DENY: capability=%s user=%s source=%s reason=%s",
            capability, user_id, source_device_id, reason,
        )
        return GuardResult(
            decision=Decision.DENY,
            reason=reason,
            denial_code=code,
        )

    async def _audit(
        self, user_id, device_id, source_device_id, task_id,
        capability, parameters, status, reason,
    ) -> None:
        try:
            async with self.session_factory() as session:
                session.add(PluginAuditModel(
                    user_id=user_id,
                    device_id=device_id,
                    source_device_id=source_device_id,
                    task_id=task_id,
                    capability=capability,
                    parameters_json=parameters,
                    result_status=status,
                    decision_reason=reason,
                ))
                await session.commit()
        except Exception:
            logger.exception("plugin_audit: write failed")


# ---------------------------------------------------------------------
# Pure helpers — replicated from the C++ side for defense-in-depth
# ---------------------------------------------------------------------

_FORBIDDEN_SHELL_CHARS = set(";&|<>$`\n\r")


def _command_allowed(command: str, prefixes: List[str]) -> bool:
    if not command or not prefixes:
        return False
    for c in command:
        if c in _FORBIDDEN_SHELL_CHARS:
            return False
    trimmed = command.strip()
    for prefix in prefixes:
        p = prefix.strip()
        if not p:
            continue
        if trimmed == p:
            return True
        if trimmed.startswith(p) and len(trimmed) > len(p):
            next_char = trimmed[len(p)]
            if next_char in (" ", "\t"):
                return True
    return False


def _extract_paths(capability: str, params: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for key in ("path", "source", "destination"):
        v = params.get(key)
        if isinstance(v, str) and v:
            paths.append(v)
    return paths


def _path_in_roots(path: str, roots: List[str]) -> bool:
    import os
    expanded = os.path.expanduser(path)
    if "/../" in expanded or expanded.endswith("/.."):
        return False
    real = os.path.realpath(expanded)
    for root in roots:
        root_real = os.path.realpath(os.path.expanduser(root))
        if real == root_real:
            return True
        if real.startswith(root_real + "/"):
            return True
    return False