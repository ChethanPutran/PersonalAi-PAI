from pai.common.enums.permission import PermissionResource

from ..models import Policy, PolicyResult, PolicyEffect


class ApprovalPolicy(Policy):
    """Require human approval when consent says so, or for always-approve resources.

    Ported from the legacy ``camera-approval`` PolicyRule: camera control always
    needs approval, regardless of any standing consent.
    """

    ALWAYS_APPROVE = {(PermissionResource.CAMERA.value, "control")}

    def __init__(self):
        super().__init__("approval-policy", priority=30, evaluator=self.evaluate)

    @classmethod
    def evaluate(cls, ctx):
        req = ctx.request
        resource_type = req.permission.split(".", 1)[0]
        if (resource_type, req.action) in cls.ALWAYS_APPROVE:
            return PolicyResult("approval-policy", PolicyEffect.APPROVAL_REQUIRED, "This action always requires approval", ["human_approval"])
        consent = ctx.consent
        if consent is not None and getattr(consent, "approval_required", False):
            return PolicyResult("approval-policy", PolicyEffect.APPROVAL_REQUIRED, "Explicit approval is required", ["human_approval"])
        return PolicyResult("approval-policy", PolicyEffect.NOT_APPLICABLE, "No approval flag")
