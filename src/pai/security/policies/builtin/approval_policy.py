from pai.common.enums.permission import PermissionResource

from ..models import Policy, PolicyResult, PolicyEffect


class ApprovalPolicy(Policy):
    """Require human approval when consent says so, or for always-approve resources.

    Ported from the legacy ``camera-approval`` PolicyRule: camera control always
    needs approval, regardless of any standing consent.
    """

    # The strictest set: (resource, action) pairs whose approval can NEVER be remembered, so a standing
    # consent can never silently allow them. Camera control, and typing/clicking for the user.
    ALWAYS_APPROVE = frozenset({
        (PermissionResource.CAMERA.value, "control"),
        (PermissionResource.INPUT.value, "control"),
    })

    def __init__(self, always_approve=None):
        # None = the strict default. Build a relaxed set with ``always_approve_for`` (never by hand).
        self.always_approve = frozenset(self.ALWAYS_APPROVE if always_approve is None else always_approve)
        super().__init__("approval-policy", priority=30, evaluator=self.evaluate)

    @classmethod
    def always_approve_for(cls, input_grant_scope: str = "once") -> frozenset:
        """The always-approve set for the configured ``security.authorization.input_grant_scope``.

        ``once`` keeps everything strict. ``session`` lets keyboard/mouse approvals be remembered for a
        session; camera control stays once-only either way. One function, so the policy and the scope
        limits shown/enforced to the user can never disagree.
        """
        if input_grant_scope == "once":
            return cls.ALWAYS_APPROVE
        if input_grant_scope == "session":
            return frozenset(p for p in cls.ALWAYS_APPROVE if p[0] != PermissionResource.INPUT.value)
        raise ValueError(f"input_grant_scope must be 'once' or 'session', got {input_grant_scope!r}")

    def evaluate(self, ctx):
        req = ctx.request
        resource_type = req.permission.split(".", 1)[0]
        if (resource_type, req.action) in self.always_approve:
            return PolicyResult("approval-policy", PolicyEffect.APPROVAL_REQUIRED, "This action always requires approval", ["human_approval"])
        consent = ctx.consent
        if consent is not None and getattr(consent, "approval_required", False):
            return PolicyResult("approval-policy", PolicyEffect.APPROVAL_REQUIRED, "Explicit approval is required", ["human_approval"])
        return PolicyResult("approval-policy", PolicyEffect.NOT_APPLICABLE, "No approval flag")
