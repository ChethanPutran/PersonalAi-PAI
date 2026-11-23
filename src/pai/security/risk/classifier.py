from dataclasses import dataclass
from enum import Enum

from pai.common.enums.permission import RiskLevel


@dataclass
class RiskAssessment:
    level: RiskLevel
    reasons: list[str]

class RiskClassifier:
    ORDER = {"low":0, "medium":1, "high":2, "critical":3}
    def __init__(self, registry):
        self.registry = registry
        
    def classify(self, request, permission):
        # Static registry override wins; otherwise the permission declaration's
        # own risk; unknown permissions are treated as high risk (fail-safe).
        level = self.registry.get(request.permission)
        if level is None and request.capability:
            level = self.registry.get(request.capability)
        if level is None:
            level = getattr(permission, "risk", None) or "high"

        if isinstance(level, str):
            level = RiskLevel(level)
        reasons = [f"Permission risk: {level}"]
        if request.action in ("write", "execute", "control"):
            level = RiskLevel(max(level, "high", key=lambda x: self.ORDER[x]))
            reasons.append(f"Action {request.action} raises risk")
        return RiskAssessment(level, reasons)