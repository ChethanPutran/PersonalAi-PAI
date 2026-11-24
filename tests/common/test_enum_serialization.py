"""Enums must serialize to their plain string values everywhere: JSON, str(), f-strings, logs, trace events,
the ledger, and the model id sent to a provider. Regression: LLM enums printed as "LLMProviderName.AIROUTER"
(trace event names), the plain-Enum ones were not JSON serializable at all, and the OpenAI/Gemini
non-streaming calls sent the member NAME ("GPT_4") instead of the model id ("gpt-4")."""
from __future__ import annotations

import enum
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
CLASS_RE = re.compile(r"^class \w+\(.*Enum.*\):", re.M)


def all_enum_classes():
    out = []
    for path in sorted((SRC / "pai").rglob("*.py")):
        if not CLASS_RE.search(path.read_text(encoding="utf-8")):
            continue
        modname = ".".join(path.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
        try:
            mod = importlib.import_module(modname)
        except Exception:                      # modules needing optional deps are covered when importable
            continue
        for name, cls in vars(mod).items():
            if isinstance(cls, type) and issubclass(cls, enum.Enum) and cls.__module__ == modname:
                out.append(cls)
    return out


ENUMS = [c for c in all_enum_classes() if list(c) and all(isinstance(m.value, str) for m in c)]


def test_the_audit_actually_finds_the_enums():
    assert len(ENUMS) >= 60                    # a silent import failure must not make this test vacuous
    names = {c.__name__ for c in ENUMS}
    assert {"LLMProviderName", "LLMModel", "TaskStatus", "EventKind", "ExecutionStatus", "PromptGuardMode"} <= names


@pytest.mark.parametrize("cls", ENUMS, ids=lambda c: f"{c.__module__.split('pai.')[-1]}.{c.__name__}")
def test_every_string_enum_serializes_to_its_value(cls):
    for member in cls:
        assert json.dumps(member) == json.dumps(member.value), "json"
        assert json.loads(json.dumps({"k": member}))["k"] == member.value
        assert str(member) == member.value, "str()"
        assert f"{member}" == member.value, "f-string"
        assert "{}".format(member) == member.value, "format()"
        assert "%s" % member == member.value, "%s"
        assert member == member.value and hash(member) == hash(member.value)


# ── the LLM enums ───────────────────────────────────────────────────────────

def test_any_gateway_model_id_behaves_like_a_plain_string_member():
    from pai.common.enums.llm import LLMModel, LLMProviderName
    m = LLMModel("openai/gpt-5.2")
    assert isinstance(m, str) and m.value == m.name == str(m) == "openai/gpt-5.2"
    assert f"llm:{LLMProviderName.AIROUTER}:{m}" == "llm:airouter:openai/gpt-5.2"
    assert json.dumps({"provider": LLMProviderName.AIROUTER, "model": m}) == '{"provider": "airouter", "model": "openai/gpt-5.2"}'
    assert m == "openai/gpt-5.2" and m is LLMModel("openai/gpt-5.2") and {m: 1}["openai/gpt-5.2"] == 1
    assert LLMModel("gpt-4") is LLMModel.GPT_4


def test_config_models_dump_enums_as_strings_in_both_modes():
    from pai.config.models.llm import LLMProviderConfig
    c = LLMProviderConfig(provider="airouter", model="anthropic/claude-haiku-5.5")
    for dumped in (c.model_dump(), c.model_dump(mode="json")):
        assert json.loads(json.dumps(dumped))["provider"] == "airouter"
        assert json.loads(json.dumps(dumped))["model_name"] == "anthropic/claude-haiku-5.5"
    assert json.loads(c.model_dump_json())["provider"] == "airouter"


# ── trace events, ledger, wire format ───────────────────────────────────────

def test_llm_trace_events_hold_plain_strings():
    from pai.common.enums.llm import LLMModel, LLMProviderName
    from pai.observability import TraceStore, Tracer

    store = TraceStore(persist_dir=None)
    tracer = Tracer(store=store)
    with tracer.start_trace("t", trace_id="enum"):
        tracer.log_llm_call(provider=LLMProviderName.AIROUTER, model=LLMModel("openai/gpt-5.2"), prompt="p", response="r")
    ev = next(e for e in store.get_trace("enum") if str(e.name).startswith("llm:"))
    assert ev.name == "llm:airouter:openai/gpt-5.2"
    assert type(ev.attributes["provider"]) is str and ev.attributes["provider"] == "airouter"
    assert type(ev.attributes["model"]) is str and ev.attributes["model"] == "openai/gpt-5.2"
    json.dumps(ev.attributes)


def test_cost_ledger_and_summary_hold_plain_strings(tmp_path):
    from pai.common.enums.llm import LLMModel, LLMProviderName
    from pai.llm.cost import CostTracker

    t = CostTracker(ledger_path=tmp_path / "u.jsonl")
    t.record(provider=LLMProviderName.AIROUTER, model=LLMModel("openai/gpt-5.2"), prompt_tokens=1, completion_tokens=1,
             total_tokens=2, cost_usd=0.1, cost_source="configured", route="chat")
    line = json.loads((tmp_path / "u.jsonl").read_text())
    assert line["provider"] == "airouter" and line["model"] == "openai/gpt-5.2"
    s = t.summary()
    assert "airouter" in s["by_provider"] and "openai/gpt-5.2" in s["by_model"]
    assert type(s["recent"][0]["provider"]) is str and type(s["recent"][0]["model"]) is str
    json.dumps(s)


async def test_openai_non_streaming_call_sends_the_model_id_not_the_member_name():
    from pai.common.enums.llm import LLMModel
    from pai.config.models.llm import LLMProviderConfig
    from pai.llm.models import LLMMessage, LLMRequest
    from pai.llm.provider import OpenAIProvider

    class Fake:
        def __init__(self):
            self.chat = self.completions = self
            self.kwargs = {}

        async def create(self, **kw):
            self.kwargs = kw
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2), id="x", model="gpt-4")

    p = OpenAIProvider(LLMProviderConfig(provider="openai", api_key="k", model_name=LLMModel.GPT_4))
    p._client = Fake()
    await p.generate(LLMRequest(messages=[LLMMessage("user", "hi")]))
    assert p._client.kwargs["model"] == "gpt-4"             # was "GPT_4"
