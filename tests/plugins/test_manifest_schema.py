"""`manifest.schema.json` must describe exactly what the code accepts.

Two validation layers exist (`pai.plugins.manifest_install`): the JSON Schema and the Pydantic models. The models
are what actually load plugins, so the schema must mirror them: same fields, same required set, same enums, same
strictness. This test fails when someone changes a model (a new field, a new enum value, a new required key) without
updating the schema, and checks that every real manifest passes both layers."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pai.common.enums.device import DeviceArchitecture, DevicePlatform, DeviceType
from pai.common.enums.permission import ConsentScope, PermissionResource, RiskLevel
from pai.common.enums.task import TaskType
from pai.common.task_controls import CancelMode
from pai.plugins import manifest_install as MI
from pai.plugins.manifest import PluginManifest
from pai.plugins.manifest_enums import PluginLanguage, PreconditionType, RuntimeKind

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "src/pai/plugins/manifest.schema.json").read_text())
MANIFESTS = sorted((REPO / "plugin_registry/plugins").glob("*/*/*/manifest.json"))


# ── every real manifest passes both layers ──────────────────────────────────

def test_manifests_were_found():
    assert len(MANIFESTS) >= 10


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.relative_to(REPO / "plugin_registry/plugins").as_posix())
def test_every_manifest_passes_json_schema_and_the_models(path):
    raw = json.loads(path.read_text())
    MI.validate_dict(raw)                       # layer 1: JSON Schema
    assert MI.load_manifest_file(path).id       # layer 1 + 2 together


def test_the_schema_is_itself_valid_draft_2020_12():
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(SCHEMA)


# ── the schema mirrors the models ───────────────────────────────────────────

def defs(name):
    return SCHEMA["$defs"][name]


# schema location -> (pydantic model name in $defs of the model schema, or None for the root, extra requirements the
# schema adds on top of the model's required fields: the model enforces those in a validator instead)
LEVELS = {
    "root": (None, {"capabilities"}),                      # model: "at least one capability" validator
    "runtime": ("PluginRuntime", set()),
    "platformSpec": ("PlatformSpec", set()),
    "capability": ("CapabilitySpec", set()),
    "precondition": ("Precondition", set()),
    "controls": ("TaskControls", set()),
    "parametersSchema": ("ParametersSchema", {"type"}),    # model defaults type to "object"
    "parameter": ("CapabilityParameter", set()),
    "changelog": ("Changelog", set()),
}


def schema_node(level):
    if level == "root":
        return SCHEMA
    if level == "changelog":
        return SCHEMA["properties"]["changelog"]
    return defs(level)


def model_node(level):
    full = PluginManifest.model_json_schema()
    name = LEVELS[level][0]
    return full if name is None else full["$defs"][name]


@pytest.mark.parametrize("level", list(LEVELS))
def test_schema_and_model_have_the_same_fields(level):
    assert set(schema_node(level)["properties"]) == set(model_node(level)["properties"]), level


@pytest.mark.parametrize("level", list(LEVELS))
def test_schema_requires_what_the_model_requires(level):
    model_required = set(model_node(level).get("required", []))
    schema_required = set(schema_node(level).get("required", []))
    assert model_required <= schema_required, f"{level}: the schema does not require {sorted(model_required - schema_required)}"
    assert schema_required - model_required <= LEVELS[level][1], \
        f"{level}: the schema requires {sorted(schema_required - model_required - LEVELS[level][1])}, which the model does not"


@pytest.mark.parametrize("level,strict", [
    ("root", True), ("runtime", True), ("platformSpec", True), ("capability", True), ("precondition", True),
    ("changelog", True), ("controls", True), ("parametersSchema", False), ("parameter", False),
])
def test_schema_is_as_strict_as_the_model_about_unknown_keys(level, strict):
    allows = schema_node(level).get("additionalProperties", True) is not False
    assert allows is (not strict), f"{level}: unknown keys {'allowed' if allows else 'rejected'} by the schema"
    if level != "controls":                       # TaskControls ignores unknown keys; the schema rejecting typos is intentional
        m = model_node(level).get("additionalProperties", True)
        assert (m is not False) is (not strict), f"{level}: model strictness changed"


ENUMS = [
    ("type", lambda: SCHEMA["properties"]["type"]["enum"], PluginLanguage),
    ("platforms", lambda: SCHEMA["properties"]["platforms"]["propertyNames"]["enum"], DevicePlatform),
    ("architectures", lambda: SCHEMA["properties"]["architectures"]["items"]["enum"], DeviceArchitecture),
    ("deviceTypes", lambda: SCHEMA["properties"]["deviceTypes"]["items"]["enum"], DeviceType),
    ("permissions", lambda: defs("permissionResource")["enum"], PermissionResource),
    ("runtime.kind", lambda: defs("runtime")["properties"]["kind"]["enum"], RuntimeKind),
    ("capability.risk", lambda: defs("capability")["properties"]["risk"]["enum"], RiskLevel),
    ("capability.taskType", lambda: defs("capability")["properties"]["taskType"]["enum"], TaskType),
    ("capability.maxGrantScope", lambda: defs("capability")["properties"]["maxGrantScope"]["enum"], ConsentScope),
    ("precondition.type", lambda: defs("precondition")["properties"]["type"]["enum"], PreconditionType),
    ("controls.cancel", lambda: defs("controls")["properties"]["cancel"]["enum"], CancelMode),
]


@pytest.mark.parametrize("name,schema_values,enum", ENUMS, ids=[e[0] for e in ENUMS])
def test_schema_enums_are_exactly_the_code_enums(name, schema_values, enum):
    assert sorted(schema_values()) == sorted(m.value for m in enum), name


def test_permissions_enum_is_shared_by_the_top_level_list():
    assert SCHEMA["properties"]["permissions"]["items"] == {"$ref": "#/$defs/permissionResource"}


def test_the_parameter_type_enum_matches_the_model():
    from typing import get_args
    from pai.plugins.manifest_models import CapabilityParameter
    assert sorted(defs("parameter")["properties"]["type"]["enum"]) == sorted(get_args(CapabilityParameter.model_fields["type"].annotation))


# ── the schema rejects what the models reject (and accepts what they accept) ─

def good() -> dict:
    return copy.deepcopy(json.loads(next(p for p in MANIFESTS if "desktop/1.2.0" in p.as_posix()).read_text()))


def mutate(fn):
    m = good()
    fn(m)
    return m


REJECTED = {
    "unknown platform key": lambda m: m["platforms"].update({"plan9": {"artifact": "a", "entrypoint": "b"}}),
    "platform without entrypoint": lambda m: m["platforms"]["linux"].pop("entrypoint"),
    "platform without artifact": lambda m: m["platforms"]["linux"].pop("artifact"),
    "unknown architecture": lambda m: m.update(architectures=["mips"]),
    "unknown device type": lambda m: m.update(deviceTypes=["fridge"]),
    "unknown permission": lambda m: m.update(permissions=["teleport"]),
    "no capabilities": lambda m: m.update(capabilities=[]),
    "capabilities missing": lambda m: m.pop("capabilities"),
    "version without patch": lambda m: m.update(version="1.0"),
    "version with a prefix": lambda m: m.update(version="v1.0.0"),
    "empty nativeModule": lambda m: m["runtime"].update(nativeModule=""),
    "unknown top-level key": lambda m: m.update(colour="red"),
    "unknown capability key": lambda m: m["capabilities"][0].update(colour="red"),
    "array parameter without items": lambda m: m["capabilities"][0]["parameters"]["properties"].update({"xs": {"type": "array"}}),
    "required on a non-object parameter": lambda m: m["capabilities"][0]["parameters"]["properties"].update({"s": {"type": "string", "required": ["a"]}}),
    "schemaVersion beyond the supported range": lambda m: m.update(schemaVersion=99),
}


@pytest.mark.parametrize("what", list(REJECTED))
def test_json_schema_rejects(what):
    with pytest.raises(MI.ManifestError):
        MI.validate_dict(mutate(REJECTED[what]))


@pytest.mark.parametrize("what", [w for w in REJECTED if w not in ("version without patch", "version with a prefix")])
def test_the_models_reject_it_too_so_the_two_layers_agree(what):
    """(Only the X.Y.Z pattern for `version` is intentionally schema-only: it is the repo's release policy.)"""
    with pytest.raises(MI.ManifestError):
        MI.validate_manifest(mutate(REJECTED[what]))


def test_extra_json_schema_keywords_in_parameters_are_accepted_like_the_model_does():
    def add(m):
        m["capabilities"][0]["parameters"]["additionalProperties"] = False
        m["capabilities"][0]["parameters"]["title"] = "extra"
        m["capabilities"][0]["parameters"]["properties"].update({"when": {"type": "string", "format": "date-time", "examples": ["x"]}})
    MI.validate_manifest(mutate(add))


def test_platform_qualified_ids_and_all_enum_values_are_accepted():
    def fill(m):
        m["architectures"] = [a.value for a in DeviceArchitecture]
        m["deviceTypes"] = [d.value for d in DeviceType if d.value != "server"]
        m["permissions"] = [p.value for p in PermissionResource]
        m["platforms"] = {p.value: {"artifact": f"{p.value}/x", "entrypoint": "create"} for p in DevicePlatform}
    MI.validate_dict(mutate(fill))
