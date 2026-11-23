from .evaluator import ScopeResult

class GenericScope:
    @staticmethod
    def evaluate(request, permission):
        return ScopeResult(True, "Generic scope accepted", {})