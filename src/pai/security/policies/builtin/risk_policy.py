from ..models import Policy, PolicyResult, PolicyEffect

class RiskPolicy(Policy):
    def __init__(self):
        super().__init__("risk-policy", priority=20, evaluator=self.evaluate)
    @staticmethod
    def evaluate(ctx):
        level = getattr(ctx.risk, "level", "low")
        if level == "critical":
            return PolicyResult("risk-policy", PolicyEffect.DENY, "Critical-risk operation blocked")
        if level == "high":
            return PolicyResult("risk-policy", PolicyEffect.APPROVAL_REQUIRED, "High-risk action requires approval", ["human_approval"])
        return PolicyResult("risk-policy", PolicyEffect.NOT_APPLICABLE, "Risk policy did not require intervention")