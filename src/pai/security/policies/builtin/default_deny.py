from ..models import Policy, PolicyResult, PolicyEffect
from ...authorization.context import AuthorizationContext


class DefaultDenyPolicy(Policy):
    """Catch-all: nothing is allowed unless the user has an active consent.

    Runs last (highest priority number). Combined with deny-overrides, an
    explicit DENY from any other policy still wins over this ALLOW.
    """

    def __init__(self):
        super().__init__("default-deny", priority=10000, evaluator=self.evaluate)

    @staticmethod
    def evaluate(ctx: AuthorizationContext) -> PolicyResult:
        consent = ctx.consent
        if consent is not None and consent.active():
            return PolicyResult(
                "default-deny", PolicyEffect.ALLOW,
                "Permission declaration and consent passed; no blocking policy matched",
            )
        return PolicyResult(
            "default-deny", PolicyEffect.APPROVAL_REQUIRED,
            "Consent is missing or inactive", ["human_approval"],
        )
