# Authoritative static registry. This cannot be overridden by LLM/planner.
from pai.common.enums.permission import RiskLevel


CAPABILITY_RISK_REGISTRY: dict[str, RiskLevel] = {
    # Shell — HIGH: never runs without the user's approval, remembered at most for the session and
    # only for that exact command (consent.binding.BOUND_PARAMETERS). Not CRITICAL: that is a hard deny.
    "shell.exec": RiskLevel.HIGH,

    # Files
    "files.delete": RiskLevel.HIGH,
    "files.write": RiskLevel.HIGH,
    "files.move": RiskLevel.MEDIUM,
    "files.copy": RiskLevel.MEDIUM,
    "files.read": RiskLevel.LOW,
    "files.list": RiskLevel.LOW,
    "files.stat": RiskLevel.LOW,

    # Camera / media
    "camera.capture": RiskLevel.MEDIUM,
    "camera.stream": RiskLevel.HIGH,

    # Desktop
    "clipboard.read": RiskLevel.MEDIUM,
    "clipboard.write": RiskLevel.MEDIUM,
    "app.launch": RiskLevel.MEDIUM,
    "audio.play": RiskLevel.LOW,
    "audio.speak": RiskLevel.LOW,
    "media.control": RiskLevel.LOW,
    "notifications.read": RiskLevel.LOW,
    "notifications.dismiss": RiskLevel.LOW,

    # Builtins
    "chat.reply": RiskLevel.LOW,
    "task.create": RiskLevel.LOW,
    "task.list": RiskLevel.LOW,
    "task.update": RiskLevel.LOW,
    "task.cancel": RiskLevel.LOW,
    "schedule.create": RiskLevel.LOW,
    "schedule.list": RiskLevel.LOW,
    "schedule.delete": RiskLevel.LOW,

    # Network
    "network.request": RiskLevel.MEDIUM,

    # Device
    "device.control": RiskLevel.HIGH,

    # System
    "system.execute": RiskLevel.CRITICAL,
}


DEFAULT_RISK = RiskLevel.MEDIUM


def classify_risk(capability: str) -> RiskLevel:
    """Return the static risk level for a capability.

    Risk is NEVER derived from LLM output or planner parameters.
    Only the static registry is authoritative.
    """
    return CAPABILITY_RISK_REGISTRY.get(capability, DEFAULT_RISK)


class RiskRegistry:
    def __init__(self, mapping=None):
        self.mapping = dict(mapping or {})
    @classmethod
    def with_defaults(cls):
        return cls(CAPABILITY_RISK_REGISTRY)
    def get(self, permission, default=None):
        return self.mapping.get(permission, default)