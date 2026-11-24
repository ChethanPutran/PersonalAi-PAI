from .models import PolicyEffect
from ..authorization.decision import DecisionEffect

class PolicyCombiner:
    def combine(self, results):
        denies = [r for r in results if r.effect == PolicyEffect.DENY]
        if denies:
            return DecisionEffect.DENY, "; ".join(r.reason for r in denies), [r.policy_id for r in denies], []
        approvals = [r for r in results if r.effect == PolicyEffect.APPROVAL_REQUIRED]
        if approvals:
            return DecisionEffect.APPROVAL_REQUIRED, "; ".join(r.reason for r in approvals), [r.policy_id for r in approvals], sum((r.obligations for r in approvals), [])
        allows = [r for r in results if r.effect == PolicyEffect.ALLOW]
        if allows:
            return DecisionEffect.ALLOW, "; ".join(r.reason for r in allows), [r.policy_id for r in allows], sum((r.obligations for r in allows), [])
        return DecisionEffect.DENY, "No policy allowed this request", ["default-deny"], []