from enum import Enum, StrEnum


class TaskType(StrEnum):
    """How a task RUNS. Declared by the capability (manifest ``taskType`` / built-in definition),
    never chosen by the LLM: it decides whether the task has a deadline (long_running and stream
    have none), whether stream frames flow, and what pause means."""
    ONE_SHOT = "one_shot"
    LONG_RUNNING = "long_running"
    STREAM = "stream"
