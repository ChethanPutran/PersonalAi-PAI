"""Personal AI System - Context-Aware Autonomous Personal Agent System."""

from pai.version import RUNTIME_VERSION

__version__ = RUNTIME_VERSION
__author__ = "PAI Team"

__all__ = [
    "TaskOrchestrator"
]


def __getattr__(name: str):
    """`pai.TaskOrchestrator` is imported on first use, not when any `pai.*` module is imported.

    Importing the orchestrator pulls in the whole runtime (database, vector store, graph DB, ...), which the
    command-line tools (`pai-validate-manifests`, `pai-sync-versions`, `pai-check-plugin-versions`,
    `pai-package-plugin`) do not need: they work with only pydantic, jsonschema, packaging and loguru.
    """
    if name == "TaskOrchestrator":
        from pai.orchestration.orchestrator import TaskOrchestrator
        return TaskOrchestrator
    raise AttributeError(f"module 'pai' has no attribute {name!r}")
