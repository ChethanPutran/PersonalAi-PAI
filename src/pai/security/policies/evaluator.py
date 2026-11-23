from .models import PolicyResult, PolicyEffect

class PolicyEvaluator:
    def __init__(self, repository):
        self.repository = repository
    def evaluate(self, context):
        results = []
        for policy in self.repository.all():
            try:
                result = policy.evaluator(context)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                results.append(PolicyResult(policy.policy_id, PolicyEffect.DENY, f"Policy error: {exc}"))
        return results