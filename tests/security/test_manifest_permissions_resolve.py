"""Every permission a shipped manifest declares must resolve to a registered permission
whose action the role policy understands - otherwise the capability is denied for everyone."""
import glob
import json

from pai.common.enums.permission import PermissionResource
from pai.security.permissions.registry import PermissionRegistry
from pai.security.policies.builtin.role_policy import ACTION_CLASS


def test_all_manifest_permissions_resolve():
    registry = PermissionRegistry()
    bad = []
    for path in glob.glob("plugin_registry/plugins/**/manifest.json", recursive=True):
        for cap in json.load(open(path)).get("capabilities", []):
            for res in cap.get("permissions", []):
                decl = next((d for d in registry.all() if d.name.split(".", 1)[0] == res), None)
                ok = decl is not None and decl.actions[0] in ACTION_CLASS and res in set(PermissionResource)
                if not ok:
                    bad.append((path, cap["id"], res))
    assert not bad, bad
