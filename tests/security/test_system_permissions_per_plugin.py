"""permissions.json is per plugin: one plugin's grant never covers another's (and legacy shared entries grant nothing)."""
import json

from pai.common.enums.permission import PermissionResource
from pai.security.permissions.declaration import clear_system_permission_cache, load_system_permissions


def _load(tmp_path, data, plugin):
    clear_system_permission_cache()
    f = tmp_path / "permissions.json"
    f.write_text(json.dumps(data))
    return load_system_permissions(str(f), plugin=plugin).permissions


def test_a_grant_belongs_to_the_plugin_it_was_given_to(tmp_path):
    data = {"pai.camera/camera": "allow", "pai.camera/filesystem": "allow", "pai.files/filesystem": "deny"}
    assert PermissionResource.FILESYSTEM in _load(tmp_path, data, "pai.camera")
    assert PermissionResource.FILESYSTEM not in _load(tmp_path, data, "pai.files")      # camera's grant is not files'
    assert _load(tmp_path, data, "pai.unknown") == frozenset()


def test_legacy_shared_entries_and_no_plugin_grant_nothing(tmp_path):
    legacy = {"camera": "allow", "filesystem": "allow"}
    assert _load(tmp_path, legacy, "pai.camera") == frozenset()
    assert _load(tmp_path, {"pai.camera/camera": "allow"}, None) == frozenset()


def test_a_plugin_prefix_is_not_a_prefix_match(tmp_path):
    assert _load(tmp_path, {"pai.screen2/screen": "allow"}, "pai.screen") == frozenset()


def test_missing_or_broken_file_grants_nothing(tmp_path):
    clear_system_permission_cache()
    assert load_system_permissions(str(tmp_path / "nope.json"), plugin="pai.camera").permissions == frozenset()
    clear_system_permission_cache()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_system_permissions(str(bad), plugin="pai.camera").permissions == frozenset()
