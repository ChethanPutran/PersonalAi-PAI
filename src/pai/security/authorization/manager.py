from pai.observability.tracer import get_tracer

from .request import AuthorizationRequest
from .context import AuthorizationContext
from .decision import AuthorizationDecision, DecisionEffect
from .evaluator import AuthorizationEvaluator


class AuthorizationManager:
    def __init__(self, permission_validator, policy_evaluator, combiner, consent_manager,
                 scope_evaluator, risk_classifier, audit_logger, token_issuer):
        self.permission_validator = permission_validator
        self.policy_evaluator = policy_evaluator
        self.combiner = combiner
        self.consent_manager = consent_manager
        self.scope_evaluator = scope_evaluator
        self.risk_classifier = risk_classifier
        self.audit_logger = audit_logger
        self.token_issuer = token_issuer

    async def authorize(self, request: AuthorizationRequest, ctx: AuthorizationContext) -> AuthorizationDecision:
        tracer = get_tracer()
        async with tracer.span(
            "authz.authorize",
            user_id=request.user_id,
            capability=request.capability,
            device_id=request.device_id,
            plugin=request.plugin_id,
        ) as span:
            try:
                permission = self.permission_validator.validate(
                    request.permission, request.plugin_id, request.action)
                ctx.permission = permission
                ctx.scope_result = self.scope_evaluator.evaluate(
                    request, permission)
                if not ctx.scope_result.allowed:
                    decision = AuthorizationEvaluator.build_decision(
                        ctx, DecisionEffect.DENY, ctx.scope_result.reason)
                    self.audit_logger.log(request, decision)
                    return decision
                ctx.risk = self.risk_classifier.classify(request, permission)
                ctx.consent = self.consent_manager.check(
                    request, permission,
                    manifest_requires_confirmation=ctx.manifest.requires_confirmation,
                )
                ctx.policy_results = self.policy_evaluator.evaluate(ctx)
                effect, reason, policy_ids, obligations = self.combiner.combine(
                    ctx.policy_results)
                # Explicit user approval resolves APPROVAL_REQUIRED, never DENY.
                if effect == DecisionEffect.APPROVAL_REQUIRED and ctx.manifest.approved:
                    effect, reason = DecisionEffect.ALLOW, f"Approved by user ({reason})"
                decision = AuthorizationEvaluator.build_decision(
                    ctx, effect, reason, policy_ids, obligations)
                decision.confirmation_reason = ctx.manifest.confirmation_reason
                if decision.allowed:
                    decision.token = self.token_issuer.issue(
                        request, permission, ctx.scope_result, ctx.risk)
                self.audit_logger.log(request, decision)
                return decision
            except Exception as exc:
                decision = AuthorizationEvaluator.build_decision(
                    ctx, DecisionEffect.DENY, f"Security evaluation error: {exc}")
                self.audit_logger.log(request, decision)
                return decision
