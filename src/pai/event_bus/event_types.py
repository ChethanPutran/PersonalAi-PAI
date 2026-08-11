from enum import Enum

class EventType(str, Enum):
    TASK_CREATED = "task.created"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    MEMORY_UPDATED = "memory.updated"
    PLUGIN_LOADED = "plugin.loaded"
    AGENT_GOAL = "agent.goal"