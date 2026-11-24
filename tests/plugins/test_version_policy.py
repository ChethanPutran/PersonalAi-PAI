"""Version control: [tool.pai] in pyproject.toml is the source of truth; code/app copies follow it and plugin
releases obey the rules (immutable released versions, monotonic versions, changelog on update, manifests valid
for the runtime/app/schema). Hermetic: builds throw-away git repositories."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pai.plugins import versioning as V

REPO = Path(__file__).resolve().parents[2]

PYPROJECT = '''[project]
name = "x"
version = "1.0.0"

[tool.pai.versions]
runtime = "1.0.0"
manifest_schema_min = 1
manifest_schema_max = 2
min_supported_app = "1.0.0"
app = "1.2.0"

[tool.pai.plugins]
registry = "plugin_registry/plugins"
base_ref = "main"
'''
VERSION_PY = 'RUNTIME_VERSION = "1.0.0"\nMANIFEST_SCHEMA_MIN = 1\nMANIFEST_SCHEMA_MAX = 2\nMIN_SUPPORTED_APP_VERSION = "1.0.0"\n'
SCHEMA = '{ "properties": {\n  "schemaVersion": { "type": "integer", "minimum": 1, "maximum": 2 },\n  "id": {} } }\n'
PUBSPEC = "name: app\nversion: 1.2.0+7\n"
DART = "const String kAppVersion = '1.2.0';\n"


def manifest(pid="foo", version="1.0.0", platform="linux", **over) -> dict:
    m = {"schemaVersion": 1, "id": pid, "name": "Foo", "version": version, "type": "native", "author": "a", "license": "MIT",
         "runtime": {"kind": "device", "nativeModule": "pai.foo", "minPaiVersion": "1.0.0"}}
    m.update(over)
    return m


def put(root: Path, pid: str, version: str, platform="linux", m=None, artifact=b"bin") -> Path:
    d = root / "plugin_registry/plugins" / pid / version / platform
    (d / "artifact").mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(m if m is not None else manifest(pid, version, platform)))
    (d / "artifact" / "lib.so").write_bytes(artifact)
    return d


def git(root: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root)}
    return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    (r / "src/pai/plugins").mkdir(parents=True)
    (r / "frontend/lib/core").mkdir(parents=True)
    (r / "pyproject.toml").write_text(PYPROJECT)
    (r / "src/pai/version.py").write_text(VERSION_PY)
    (r / "src/pai/plugins/manifest.schema.json").write_text(SCHEMA)
    (r / "frontend/pubspec.yaml").write_text(PUBSPEC)
    (r / "frontend/lib/core/app_version.dart").write_text(DART)
    put(r, "foo", "1.0.0")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "release")
    git(r, "checkout", "-q", "-b", "feature")
    return r


def rules(vs) -> set[str]:
    return {v.rule for v in vs}


# ── policy loading ──────────────────────────────────────────────────────────

def test_policy_is_read_from_pyproject(repo):
    p = V.load_policy(repo)
    assert (p.runtime, p.manifest_schema_min, p.manifest_schema_max, p.min_supported_app, p.app) == ("1.0.0", 1, 2, "1.0.0", "1.2.0")
    assert p.registry == "plugin_registry/plugins" and p.tag_for("foo", "1.2.3") == "plugin/foo/v1.2.3"
    assert p.immutable_released_versions and p.require_changelog_on_update


@pytest.mark.parametrize("edit,match", [
    (lambda s: s.replace("[tool.pai.versions]", "[tool.pai.nothing]"), "no \\[tool.pai.versions\\]"),
    (lambda s: s.replace('runtime = "1.0.0"', 'runtime = "banana"'), "not X.Y.Z"),
    (lambda s: s.replace("manifest_schema_max = 2", "manifest_schema_max = 0"), "invalid"),
    (lambda s: s.replace('min_supported_app = "1.0.0"', 'min_supported_app = "3.0.0"'), "newer than app"),
    (lambda s: s.replace('base_ref = "main"', 'base_ref = "main"\nbogus = 1'), "unknown keys"),
    (lambda s: s.replace('app = "1.2.0"\n', ""), "missing: app"),
])
def test_a_bad_policy_is_a_clear_error(repo, edit, match):
    (repo / "pyproject.toml").write_text(edit(PYPROJECT))
    with pytest.raises(V.PolicyError, match=match):
        V.load_policy(repo)


# ── sync: code and app follow pyproject ─────────────────────────────────────

def test_in_sync_tree_has_no_drift(repo):
    assert V.sync(V.load_policy(repo), repo) == []


@pytest.mark.parametrize("file,old,new,what", [
    ("src/pai/version.py", 'RUNTIME_VERSION = "1.0.0"', 'RUNTIME_VERSION = "0.9.0"', "RUNTIME_VERSION"),
    ("src/pai/version.py", "MANIFEST_SCHEMA_MAX = 2", "MANIFEST_SCHEMA_MAX = 3", "MANIFEST_SCHEMA_MAX"),
    ("src/pai/version.py", "MANIFEST_SCHEMA_MIN = 1", "MANIFEST_SCHEMA_MIN = 2", "MANIFEST_SCHEMA_MIN"),
    ("src/pai/version.py", 'MIN_SUPPORTED_APP_VERSION = "1.0.0"', 'MIN_SUPPORTED_APP_VERSION = "0.1.0"', "MIN_SUPPORTED_APP_VERSION"),
    ("pyproject.toml", '[project]\nname = "x"\nversion = "1.0.0"', '[project]\nname = "x"\nversion = "0.1.0"', "[project].version"),
    ("src/pai/plugins/manifest.schema.json", '"maximum": 2', '"maximum": 5', "schemaVersion maximum"),
    ("frontend/pubspec.yaml", "version: 1.2.0+7", "version: 1.1.0+7", "version (before +build)"),
    ("frontend/lib/core/app_version.dart", "'1.2.0'", "'1.1.0'", "kAppVersion"),
])
def test_every_copy_is_checked_and_write_repairs_it(repo, file, old, new, what):
    path = repo / file
    path.write_text(path.read_text().replace(old, new))
    policy = V.load_policy(repo)
    drift = V.sync(policy, repo)
    assert [d.what for d in drift] == [what] and drift[0].file == file
    assert V.sync(policy, repo, write=True)                      # reports what it fixed...
    assert V.sync(policy, repo) == []                            # ...and the tree is now in sync
    assert path.read_text().count(old) >= 1


def test_write_keeps_the_pubspec_build_number(repo):
    (repo / "frontend/pubspec.yaml").write_text("name: app\nversion: 0.0.1+42\n")
    V.sync(V.load_policy(repo), repo, write=True)
    assert (repo / "frontend/pubspec.yaml").read_text() == "name: app\nversion: 1.2.0+42\n"


def test_a_missing_target_file_is_reported(repo):
    (repo / "frontend/pubspec.yaml").unlink()
    assert [d.file for d in V.sync(V.load_policy(repo), repo)] == ["frontend/pubspec.yaml"]


def test_the_real_repository_is_in_sync():
    assert [str(d) for d in V.sync(V.load_policy(REPO), REPO)] == []
    import pai.version as pv
    p = V.load_policy(REPO)
    assert (pv.RUNTIME_VERSION, pv.MANIFEST_SCHEMA_MIN, pv.MANIFEST_SCHEMA_MAX, pv.MIN_SUPPORTED_APP_VERSION) == \
        (p.runtime, p.manifest_schema_min, p.manifest_schema_max, p.min_supported_app)


# ── manifests ───────────────────────────────────────────────────────────────

def test_good_manifests_pass_including_platform_qualified_ids(repo):
    put(repo, "camera", "1.0.0", "android", manifest("camera_android", "1.0.0", "android"))
    assert V.check_manifests(V.load_policy(repo), repo) == []


@pytest.mark.parametrize("rule,mutate", [
    ("version-matches-folder", lambda m: m.update(version="1.0.1")),
    ("id-matches-folder", lambda m: m.update(id="other")),
    ("schema-version", lambda m: m.update(schemaVersion=3)),
    ("schema-version", lambda m: m.update(schemaVersion=0)),
    ("minPaiVersion-not-released", lambda m: m["runtime"].update(minPaiVersion="2.0.0")),
    ("minAppVersion-not-released", lambda m: m["runtime"].update(minAppVersion="9.0.0")),
    ("minPaiVersion-format", lambda m: m["runtime"].update(minPaiVersion="soon")),
])
def test_manifest_rules(repo, rule, mutate):
    m = manifest()
    mutate(m)
    put(repo, "foo", "1.0.0", m=m)
    assert rule in rules(V.check_manifests(V.load_policy(repo), repo))


def test_non_semver_folder_and_broken_json(repo):
    put(repo, "foo", "v2", m=manifest("foo", "v2"))
    (repo / "plugin_registry/plugins/foo/1.0.0/linux/manifest.json").write_text("{nope")
    r = rules(V.check_manifests(V.load_policy(repo), repo))
    assert {"version-format", "manifest-json"} <= r


def test_requirements_at_or_below_this_repo_are_fine(repo):
    m = manifest()
    m["runtime"].update(minPaiVersion="1.0.0", minAppVersion="1.2.0")
    put(repo, "foo", "1.0.0", m=m)
    assert V.check_manifests(V.load_policy(repo), repo) == []


# ── released versions are immutable ─────────────────────────────────────────

def check(repo, **kw):
    return V.check_released(V.load_policy(repo), repo, **kw)


def test_no_change_is_fine(repo):
    assert check(repo) == []


def test_modifying_a_released_artifact_or_manifest_is_refused(repo):
    d = repo / "plugin_registry/plugins/foo/1.0.0/linux"
    (d / "artifact/lib.so").write_bytes(b"patched")
    v = check(repo)
    assert rules(v) == {"released-version-immutable"} and "artifact/lib.so" in v[0].path and "publish a new version" in v[0].message


def test_committed_changes_count_too_not_only_working_tree_edits(repo):
    (repo / "plugin_registry/plugins/foo/1.0.0/linux/manifest.json").write_text(json.dumps(manifest(description="edited")))
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "edit a released manifest")
    assert rules(check(repo)) == {"released-version-immutable"}


def test_adding_a_file_to_or_deleting_from_a_released_version_is_refused(repo):
    d = repo / "plugin_registry/plugins/foo/1.0.0/linux"
    (d / "artifact/extra.so").write_bytes(b"x")
    (d / "artifact/lib.so").unlink()
    messages = " ".join(v.message for v in check(repo))
    assert "added to" in messages and "deleted from" in messages


def test_removing_a_whole_released_version_is_refused(repo):
    shutil.rmtree(repo / "plugin_registry/plugins/foo/1.0.0")
    assert any("was removed" in v.message for v in check(repo))


def test_a_new_higher_version_with_a_changelog_is_the_way_to_change_a_plugin(repo):
    put(repo, "foo", "1.0.1", m=manifest("foo", "1.0.1", changelog={"summary": "fix"}), artifact=b"new")
    assert check(repo) == []


def test_new_version_must_be_higher_than_every_existing_one(repo):
    put(repo, "foo", "1.1.0", m=manifest("foo", "1.1.0", changelog={"summary": "x"}))
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "1.1.0 released"); git(repo, "checkout", "-q", "main"); git(repo, "merge", "-q", "feature")
    git(repo, "checkout", "-q", "-b", "later")
    put(repo, "foo", "1.0.5", m=manifest("foo", "1.0.5", changelog={"summary": "x"}))
    assert rules(check(repo)) == {"new-version-higher"}


def test_an_update_needs_a_changelog_but_a_brand_new_plugin_does_not(repo):
    put(repo, "foo", "1.0.1", m=manifest("foo", "1.0.1"))                 # update without changelog
    put(repo, "bar", "1.0.0", m=manifest("bar", "1.0.0"))                 # brand-new plugin
    v = check(repo)
    assert rules(v) == {"changelog-required"} and "foo/1.0.1" in v[0].path
    put(repo, "foo", "1.0.1", m=manifest("foo", "1.0.1", changelog={"summary": "   "}))
    assert rules(check(repo)) == {"changelog-required"}                   # a blank summary does not count


def test_every_platform_of_a_new_version_is_checked_for_a_changelog(repo):
    put(repo, "foo", "1.0.1", "linux", manifest("foo", "1.0.1", changelog={"summary": "ok"}))
    put(repo, "foo", "1.0.1", "android", manifest("foo_android", "1.0.1", "android"))
    assert [v.path for v in check(repo)] == ["plugin_registry/plugins/foo/1.0.1/android/manifest.json"]


def test_the_rules_can_be_switched_off_in_pyproject(repo):
    (repo / "pyproject.toml").write_text(PYPROJECT + "immutable_released_versions = false\nrequire_changelog_on_update = false\n")
    (repo / "plugin_registry/plugins/foo/1.0.0/linux/artifact/lib.so").write_bytes(b"patched")
    put(repo, "foo", "1.0.1", m=manifest("foo", "1.0.1"))
    assert check(repo) == []


def test_a_missing_base_ref_is_a_git_error_not_a_pass(repo):
    with pytest.raises(V.GitError):
        check(repo, base_ref="origin/does-not-exist")


# ── command line ────────────────────────────────────────────────────────────

def test_cli_exit_codes_and_github_annotations(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert V.sync_main([]) == 0 and V.check_main([]) == 0
    (repo / "plugin_registry/plugins/foo/1.0.0/linux/artifact/lib.so").write_bytes(b"patched")
    assert V.check_main(["--github"]) == 1
    out = capsys.readouterr().out
    assert "::error file=plugin_registry/plugins/foo/1.0.0/linux/artifact/lib.so,title=plugin version::" in out
    assert V.check_main(["--no-git"]) == 0                                       # manifests alone are fine
    assert V.check_main(["--base", "origin/nope"]) == 2                          # cannot compare = error, never a silent pass
    (repo / "src/pai/version.py").write_text(VERSION_PY.replace('"1.0.0"', '"0.0.1"', 1))
    assert V.sync_main(["--github"]) == 1 and "::error file=src/pai/version.py,title=version drift::" in capsys.readouterr().out
    assert V.sync_main(["--write"]) == 0 and V.sync_main([]) == 0


def test_cli_without_a_pyproject_section_is_a_usage_error(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.chdir(tmp_path)
    assert V.sync_main([]) == 2 and V.check_main(["--no-git"]) == 2


def test_the_real_repository_passes_the_manifest_checks():
    assert [str(v) for v in V.check_manifests(V.load_policy(REPO), REPO)] == []
