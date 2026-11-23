from ..models import Policy, PolicyResult, PolicyEffect

# Config roles grant coarse verbs (read/write/delete/execute/manage); map each
# fine-grained PermissionAction onto the verb a role must hold.
ACTION_CLASS = {
    "discover": "read", "read": "read", "request": "read",
    "write": "write", "create": "write", "update": "write", "share": "write",
    "delete": "delete",
    "execute": "execute", "control": "execute",
    "manage": "manage",
}


class RolePolicy(Policy):
    """RBAC: the acting user must hold a role granting the action's verb.

    ``role_permissions`` is ``{role: {verbs}}`` from ``security.authorization.roles``.
    User roles come from the TRUSTED request context (``user_roles``); absent
    that, ``default_role`` applies (least privilege by config). Unknown action
    verbs fail closed.
    """

    def __init__(self, role_permissions: dict[str, set[str]], default_role: str):
        super().__init__("role-policy", priority=5, evaluator=self.evaluate)
        self.role_permissions = {str(r): set(v) for r, v in role_permissions.items()}
        self.default_role = str(default_role)

    def evaluate(self, ctx):
        req = ctx.request
        verb = ACTION_CLASS.get(req.action.value)
        roles = [str(r) for r in (req.context.get("user_roles") or [self.default_role])]
        if verb is None:
            return PolicyResult(self.policy_id, PolicyEffect.DENY, f"Unknown action '{req.action.value}'")
        if any(verb in self.role_permissions.get(r, ()) for r in roles):
            return PolicyResult(self.policy_id, PolicyEffect.NOT_APPLICABLE, "Role grants action")
        return PolicyResult(
            self.policy_id, PolicyEffect.DENY,
            f"Role(s) {roles} may not '{verb}' (action '{req.action.value}')",
        )
