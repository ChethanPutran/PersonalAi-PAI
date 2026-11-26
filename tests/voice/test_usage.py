import time

from pai.observability.store import TraceStore
from pai.observability.tracer import Tracer
from pai.voice import usage


def test_collect_summarize_and_trace_cost():
    with usage.collect() as used:
        usage.report(model="m", units=2.5, unit="seconds", cost_usd=0.001)
        usage.report(model="m", units=1.0, unit="seconds", cost_usd=None)
    usage.report(model="ignored", units=9, unit="seconds", cost_usd=1)       # outside collect(): dropped
    s = usage.summarize(used)
    assert s == {"model": "m", "unit": "seconds", "units": 3.5, "cost_usd": 0.001}

    store = TraceStore(persist_dir=None)
    tr = Tracer(store)
    tr.record_span("t1", "voice.stt", start=time.time() - 1, attributes={"user_id": "u", **s})
    tr.record_span("t1", "voice.tts", start=time.time(), attributes={"user_id": "u", "units": 10, "unit": "characters", "cost_usd": 0.002})
    assert store.get_summary("t1").cost_usd == 0.003
