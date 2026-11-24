from .decision import AuthorizationDecision

class AuthorizationDenied(PermissionError):
    def __init__(self, decision: AuthorizationDecision):
        self.decision = decision
        super().__init__(f"Authorization denied: {decision.reason}")

def enforce(decision: AuthorizationDecision) -> None:
    if not decision.allowed:
        raise AuthorizationDenied(decision)