from pai.common.enums.permission import PermissionResource

from ..models import Policy, PolicyResult, PolicyEffect


class SystemRestrictionsPolicy(Policy):
    """Hard denies that no consent can override.

    Ported from the legacy ``deny-shell`` PolicyRule: shell execution is denied
    by default. Add resources/actions here (or load from config) to extend.
    """

    BLOCKED = {"system.shutdown", "system.format", "security.disable"}
    # (resource-type, action) pairs that are always denied.
    BLOCKED_RESOURCE_ACTIONS = {(PermissionResource.SHELL.value, "execute")}

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
