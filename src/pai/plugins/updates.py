"""Update discovery: "device has X@a installed - what can it move to, and what changes?"

Pure functions over a `PluginRegistry`; no I/O, so they are cheap to unit-test.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pai.plugins.manifest import PluginManifest
from pai.plugins.registry import PluginRegistry
from pai.version import is_newer, version_key


def capability_diff(old: Optional[PluginManifest], new: PluginManifest) -> Dict[str, List[str]]:
    """Capabilities added / removed / whose parameter schema changed between two versions."""
    old_caps = {c.id: c for c in (old.capabilities if old else [])}
    new_caps = {c.id: c for c in new.capabilities}
    changed = [
        cid for cid, c in new_caps.items()
        if cid in old_caps
        and c.parameters.model_dump(exclude_none=True) != old_caps[cid].parameters.model_dump(exclude_none=True)
    ]
    return {
        "added": sorted(set(new_caps) - set(old_caps)),
        "removed": sorted(set(old_caps) - set(new_caps)),
        "changed": sorted(changed),
    }


def _changelog_entry(m: PluginManifest) -> Dict[str, Any]:
    cl = m.changelog
    return {
        "version": m.version,
        "summary": cl.summary if cl else "",
        "features": list(cl.features) if cl else [],
        "fixes": list(cl.fixes) if cl else [],
        "breaking": list(cl.breaking) if cl else [],
    }


def version_summary(registry: PluginRegistry, plugin_id: str, app_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every version with its compatibility for this runtime + app, newest first."""
    out = []
    for info in registry.list_versions(plugin_id):
        m = registry.get_manifest(plugin_id, info.version)
        compat = registry.entry_compatibility(m, app_version)
        out.append({
            "version": info.version,
            "capabilities": list(info.capabilities),
            "changelog": _changelog_entry(m),
            "minPaiVersion": m.runtime.minPaiVersion,
            "minAppVersion": m.runtime.minAppVersion,
            "schemaVersion": m.schemaVersion,
            **{f"compat_{k}": v for k, v in compat.to_dict().items()},
        })
    return out


def build_update_info(
    registry: PluginRegistry,
    plugin_id: str,
    installed_version: Optional[str],
    app_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Update status of one installed plugin.

    ``latest_version`` is the newest version this device can really run (runtime AND app
    compatible), so the Update button never offers something the install would reject. A
    newer-but-blocked version is reported in ``blocked`` with the reason, so the UI can say
    "2.0.0 needs app 1.4".
    """
    infos = registry.list_versions(plugin_id)          # newest first
    base: Dict[str, Any] = {
        "plugin_id": plugin_id,
        "installed_version": installed_version,
        "latest_version": None,
        "update_available": False,
        "blocked": None,
        "changelog": [],
        "capabilities": {"added": [], "removed": [], "changed": []},
    }
    if not infos:
        return base

    usable = [i for i in infos if registry.entry_compatibility(
        registry.get_manifest(plugin_id, i.version), app_version).compatible]
    newest = infos[0]

    if usable:
        base["latest_version"] = usable[0].version
    if newest.version != (usable[0].version if usable else None) and is_newer(newest.version, installed_version or "0"):
        compat = registry.entry_compatibility(registry.get_manifest(plugin_id, newest.version), app_version)
        if not compat.compatible:
            base["blocked"] = {"version": newest.version, **compat.to_dict()}

    target = usable[0] if usable else None
    if target is None or not installed_version or not is_newer(target.version, installed_version):
        return base

    new_m = registry.get_manifest(plugin_id, target.version)
    old_m = registry.get_manifest(plugin_id, installed_version)   # None if server no longer ships it
    base["update_available"] = True
    base["capabilities"] = capability_diff(old_m, new_m)
    # Release notes of every version the user is skipping over, oldest first.
    skipped = [i for i in infos if is_newer(i.version, installed_version) and not is_newer(i.version, target.version)]
    skipped.sort(key=lambda i: version_key(i.version))
    base["changelog"] = [_changelog_entry(registry.get_manifest(plugin_id, i.version)) for i in skipped]
    return base
