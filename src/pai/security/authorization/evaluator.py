from pai.common.enums.permission import RiskLevel
from .context import AuthorizationContext
from .decision import AuthorizationDecision, DecisionEffect

class AuthorizationEvaluator:
    """
    Security check	What it checks
    Identity	    Is this agent/task/plugin legitimate?
    Permission	    Does it have filesystem.read?
    Policy	        Is file reading permitted under system rules?
    Scope	        Is this file inside the allowed directory?
    Consent	        Has the user granted filesystem access?
    Capability	    Does the task have delegated authority to read this file?
    Risk	        Is this request acceptable from a risk perspective?
    Device	        Is the target device trusted and authorized?
    Context	        Was the request user-initiated, autonomous, background, etc.?
    """
    @staticmethod
    def build_decision(ctx: AuthorizationContext, effect: DecisionEffect, reason: str, policy_ids=None, obligations=None):
        risk_level = getattr(ctx.risk, "level", ctx.risk) or RiskLevel.LOW
        return AuthorizationDecision(
            effect=effect, reason=reason, request_id=ctx.request.request_id,
            risk_level=risk_level, policy_ids=list(policy_ids or []),
            obligations=list(obligations or [])
        )