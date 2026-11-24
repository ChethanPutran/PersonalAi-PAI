"""Preset demo DAGs (use the admin.test.* capabilities)."""
from __future__ import annotations

from typing import List

from pai.api.admin.models import DemoInfo, DemoKind, NodeSpec
from pai.tasks.models import TaskType

DEMOS: List[DemoInfo] = [
    DemoInfo(kind=DemoKind.FANOUT, title="Fan-out + join",
             description="Three independent sleeps run in parallel; a joiner starts when all finish."),
    DemoInfo(kind=DemoKind.CHAIN, title="Sequential chain",
             description="Three tasks that must run strictly one after another."),
    DemoInfo(kind=DemoKind.LONG_RUNNING, title="Long-running task",
             description="A 90 s task you can kill, next to a quick independent task."),
    DemoInfo(kind=DemoKind.FAILURE_SKIP, title="Failure → skipped dependents",
             description="One task fails; its dependent is skipped; independent tasks still complete."),
]


def _sleep(i: str, seconds: float, deps: List[str] | None = None, kind: TaskType = TaskType.ONE_SHOT) -> NodeSpec:
    return NodeSpec(id=i, capability="admin.test.sleep", parameters={"seconds": seconds, "label": i},
                    depends_on=deps or [], type=kind, name=f"sleep {seconds:g}s ({i})")


def _echo(i: str, msg: str, deps: List[str] | None = None) -> NodeSpec:
    return NodeSpec(id=i, capability="admin.test.echo", parameters={"message": msg},
                    depends_on=deps or [], name=f"echo ({i})")


def build_demo(kind: DemoKind, scale: float = 1.0) -> List[NodeSpec]:
    """Demo DAG for ``kind``. ``scale`` shrinks every duration (0.05 = 20x faster)."""
    def t(seconds: float) -> float:
        return max(0.05, round(seconds * scale, 3))

    if kind == DemoKind.FANOUT:
        return [_sleep("a", t(4)), _sleep("b", t(7)), _sleep("c", t(10)), _echo("join", "all done", ["a", "b", "c"])]
    if kind == DemoKind.CHAIN:
        return [_sleep("step1", t(3)), _sleep("step2", t(3), ["step1"]), _echo("step3", "chain complete", ["step2"])]
    if kind == DemoKind.LONG_RUNNING:
        return [_sleep("long", t(90), kind=TaskType.LONG_RUNNING), _echo("quick", "I am independent")]
    if kind == DemoKind.FAILURE_SKIP:
        return [
            _echo("ok", "this one succeeds"),
            NodeSpec(id="boom", capability="admin.test.fail",
                     parameters={"after_seconds": t(2), "message": "boom (on purpose)"}, name="fails after 2s"),
            _echo("blocked", "never runs", ["boom"]),
            _sleep("independent", t(5)),
        ]
    raise ValueError(kind)
