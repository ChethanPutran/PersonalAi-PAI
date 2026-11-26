from pai.common.enums.permission import PermissionResource

from ..models import Policy, PolicyResult, PolicyEffect


class SystemRestrictionsPolicy(Policy):
    """Hard denies that no consent can override.

    Shell execution used to be here. It is now a HIGH-risk action that is asked about every time
    (or once per session, bound to the exact command), see ``consent.binding.BOUND_PARAMETERS``.
    Add resources/actions here (or load from config) to extend.
    """

    BLOCKED = {"system.shutdown", "system.format", "security.disable"}
    # (resource-type, action) pairs that are always denied.
    BLOCKED_RESOURCE_ACTIONS: set = set()

    def __init__(self):
        super().__init__("system-restrictions", priority=0, evaluator=self.evaluate)

    @classmethod
    def evaluate(cls, ctx):
        req = ctx.request
        resource_type = req.permission.split(".", 1)[0]
        if (
            req.action in cls.BLOCKED
            or req.permission in cls.BLOCKED
            or (resource_type, req.action) in cls.BLOCKED_RESOURCE_ACTIONS
        ):
            return PolicyResult("system-restrictions", PolicyEffect.DENY, "Operation is blocked by system restrictions")
        return PolicyResult("system-restrictions", PolicyEffect.NOT_APPLICABLE, "No system restriction matched")
