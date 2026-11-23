from pai.common.enums.permission import Origin

from ..models import Policy, PolicyResult, PolicyEffect

SENSITIVE_ACTIONS = {"write", "create", "update", "delete", "execute", "control", "share", "manage"}


class OriginPolicy(Policy):
    """Autonomous / scheduled requests are not a user's tap.

    A sensitive action with no user in the loop (agent-autonomous, scheduled,
    plugin-initiated) needs explicit approval; ``user_direct`` is unaffected.
    """

    def __init__(self):
        super().__init__("origin-policy", priority=25, evaluator=self.evaluate)

    @staticmethod
    def evaluate(ctx):
        req = ctx.request
        if req.origin != Origin.USER_DIRECT and req.action.value in SENSITIVE_ACTIONS:
            return PolicyResult(
                "origin-policy", PolicyEffect.APPROVAL_REQUIRED,
                f"'{req.action.value}' initiated by {req.origin.value} needs approval",
                ["human_approval"],
            )
        return PolicyResult("origin-policy", PolicyEffect.NOT_APPLICABLE, "Origin is acceptable")
