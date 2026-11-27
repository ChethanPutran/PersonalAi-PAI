"""Glue between the document index, the user's devices and their files-plugin settings."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pai.documents.index import FILES_PLUGIN_IDS, DocumentIndex, policy_from_effective
from pai.security.remote_access import remote_access_denial


class DocumentService:
    def __init__(self, index: DocumentIndex, plugin_manager: Any, device_manager: Any) -> None:
        self.index = index
        self.plugins = plugin_manager
        self.devices = device_manager

    async def files_plugin_id(self, device_id: str) -> Optional[str]:
        installed = {p["plugin_id"] for p in await self.plugins.list_device_plugins(device_id) if p.get("is_installed")}
        return next((pid for pid in FILES_PLUGIN_IDS if pid in installed), None)

    async def policy_for(self, device_id: str) -> Optional[Dict[str, Any]]:
        """The device's CURRENT path policy, or None when the files plugin is missing or indexing is off."""
        pid = await self.files_plugin_id(device_id)
        if pid is None:
            return None
        state = await self.plugins.get_device_config(device_id, pid)
        return policy_from_effective(state.get("effective") or {})

    async def search_policies(self, user_id: str, source_device_id: Optional[str],
                              device_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Devices this request may search: the user's own, indexing on, and (acting from another device or
        the web/an agent) the target's "allow remote plugin use" switch on."""
        out: Dict[str, Dict[str, Any]] = {}
        candidates: List[str] = [device_id] if device_id else [d.id for d in await self.devices.list_devices(user_id)]
        for did in candidates:
            if await remote_access_denial(self.devices, user_id=user_id, target_device_id=did,
                                          source_device_id=source_device_id, kind="invoke") is not None:
                continue
            policy = await self.policy_for(did)
            if policy is not None:
                out[did] = policy
        return out

    async def search(self, user_id: str, query: str, *, source_device_id: Optional[str], limit: int = 5,
                     device_id: Optional[str] = None) -> List[Dict[str, Any]]:
        policies = await self.search_policies(user_id, source_device_id, device_id)
        return await self.index.search(user_id, query, limit=limit, policies=policies)
