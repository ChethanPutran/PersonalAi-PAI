from dataclasses import dataclass

@dataclass
class ScopeResult:
    allowed: bool
    reason: str
    effective_scope: dict

from .filesystem import FilesystemScope
from .network import NetworkScope
from .device import DeviceScope
from .generic import GenericScope

class ScopeEvaluator:
    def evaluate(self, request, permission):
        kind = permission.scopes[0] if permission.scopes else "generic"
        evaluators = {"filesystem": FilesystemScope, "network": NetworkScope, "device": DeviceScope}
        evaluator = evaluators.get(kind, GenericScope)
        return evaluator.evaluate(request, permission)
