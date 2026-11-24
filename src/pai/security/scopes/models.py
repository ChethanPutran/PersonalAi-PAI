from enum import StrEnum


class PolicyScope(StrEnum):
    GLOBAL = "global"
    USER = "user"
    PLUGIN = "plugin"
    DEVICE = "device"
    SESSION = "session"
    TASK = "task"
