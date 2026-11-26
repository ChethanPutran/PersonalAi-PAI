"""ONE validation service for scheduled actions (REST, chat builtin, consent, firing).

A schedule is a promise to run something later WITHOUT the user watching, so everything that
could be wrong is checked up front and the reasons are returned as field errors:

  * the capability exists in the user's live capability index and may be scheduled at all;
  * the target device belongs to the user and provides it (and, if none is given, a connected
    one that does is picked);
  * the parameters satisfy the capability's JSON schema (same check the planner uses);
  * timing is sane (future ``fire_at``, valid cron/timezone, >= 60 s between plugin runs);
  * a DRY RUN of the real authorization pipeline, as a scheduled run, says whether the action
    is blocked (never schedulable), needs the user's standing consent, or can run freely.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field

from pai.common.enums.permission import Origin
from pai.security.authorization.context import ScopeContext
from pai.security.authorization.decision import DecisionEffect
from pai.security.consent.binding import BOUND_PARAMETERS

REMINDER_CAPABILITY = "system.notifications.show"     # the classic reminder: always schedulable
DEVICE_EXECUTOR = "device_executor"
MIN_PLUGIN_INTERVAL_SECONDS = 60
MIN_REMINDER_INTERVAL_SECONDS = 10
MAX_PARAMETERS_BYTES = 16_384
# A schedule must not be able to create/delete schedules or tasks (self-replication).
_NOT_SCHEDULABLE_PREFIXES = ("schedule.", "task.")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

Risk = Literal["low", "medium", "high", "critical"]


class FieldError(BaseModel):
    field: str
    message: str


class ScheduleValidationError(Exception):
    """Carries every problem found (not just the first)."""

    def __init__(self, errors: List[FieldError]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in errors))


class ScheduleSpec(BaseModel):
    """What is being validated: the user-controlled part of a schedule."""
    title: str = ""
    kind: Literal["one_shot", "cron", "interval"]
    fire_at: Optional[datetime] = None
    cron: Optional[str] = None
    interval_seconds: Optional[int] = None
    timezone: str = "UTC"
    capability: str = REMINDER_CAPABILITY
    parameters: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "ScheduleSpec":
        fire_at = row.fire_at
        if fire_at is not None and fire_at.tzinfo is None:
            # Stored as UTC; SQLite hands it back without a zone. Left naive, _check_timing would read it
            # as the schedule's LOCAL time (5.5 h off in Asia/Kolkata) and call a future time "in the past".
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        return cls(title=row.title, kind=row.kind, fire_at=fire_at, cron=row.cron,
                   interval_seconds=row.interval_seconds, timezone=row.timezone or "UTC",
                   capability=row.capability, parameters=dict(row.parameters or {}),
                   target_device_id=row.target_device_id)


class Preflight(BaseModel):
    """Outcome of a dry-run authorization as a scheduled run."""
    effect: Literal["allow", "approval_required", "deny"]
    risk: Risk = "low"
    reason: str = ""

    @property
    def requires_consent(self) -> bool:
        return self.effect == "approval_required"

    @property
    def blocked(self) -> bool:
        return self.effect == "deny" or self.risk == "critical"

    @property
    def blocked_reason(self) -> Optional[str]:
        if self.effect == "deny":
            return self.reason or "blocked by security policy"
        if self.risk == "critical":
            return "critical-risk actions cannot be scheduled"
        return None


class ValidatedSchedule(BaseModel):
    """The normalized result: what to store."""
    capability: str
    target_device_id: Optional[str]
    plugin_id: Optional[str]
    executor: Optional[str]
    risk: Risk
    requires_consent: bool
    parameters: Dict[str, Any]
    fire_at: Optional[datetime]


class SchedulableCapability(BaseModel):
    """One entry of GET /schedules/capabilities."""
    id: str
    description: str = ""
    plugin_id: str = ""
    device_ids: List[str] = Field(default_factory=list)
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)
    requires_consent: bool = False
    risk: Risk = "low"
    schedulable: bool = True
    blocked_reason: Optional[str] = None


class FireTarget(BaseModel):
    """Where a due schedule will run, resolved NOW (the index may have changed since creation)."""
    plugin_id: str
    device_id: str
    executor_id: str
    preflight: Preflight

    @property
    def requires_device(self) -> bool:
        return self.executor_id == DEVICE_EXECUTOR


def _max_risk(a: str, b: str) -> Risk:
    return a if _RISK_ORDER.get(a, 0) >= _RISK_ORDER.get(b, 0) else b       # type: ignore[return-value]


class ScheduleValidator:
    def __init__(self, *, capability_router: Any, device_manager: Any, security_manager: Any) -> None:
        self.router = capability_router
        self.devices = device_manager
        self.security = security_manager

    # ------------------------------------------------------------------ catalog

    async def catalog(self, user_id: str) -> List[SchedulableCapability]:
        index = await self.router.describe_capabilities_with_schema(user_id)
        out: List[SchedulableCapability] = []
        reminder = index.get(REMINDER_CAPABILITY) or {}
        out.append(SchedulableCapability(
            id=REMINDER_CAPABILITY,
            description=reminder.get("description") or "Show a reminder notification on your device.",
            plugin_id=(reminder.get("targets") or [{}])[0].get("plugin_id", ""),
            device_ids=[t["device_id"] for t in reminder.get("targets", [])],
            parameters_schema=reminder.get("parameters") or {"type": "object", "properties": {}},
            requires_consent=False, risk="low", schedulable=True))
        for cap_id in sorted(index):
            if cap_id == REMINDER_CAPABILITY or cap_id.startswith(_NOT_SCHEDULABLE_PREFIXES):
                continue
            entry = index[cap_id]
            targets = entry.get("targets") or []
            if not targets:
                continue
            first = targets[0]
            pre = await self.preflight(
                user_id=user_id, capability=cap_id, plugin_id=first["plugin_id"],
                device_id=first["device_id"], executor_id=first["executor_id"], parameters={})
            out.append(SchedulableCapability(
                id=cap_id, description=entry.get("description") or "",
                plugin_id=first["plugin_id"], device_ids=sorted({t["device_id"] for t in targets}),
                parameters_schema=entry.get("parameters") or {"type": "object", "properties": {}},
                requires_consent=pre.requires_consent, risk=pre.risk,
                schedulable=not pre.blocked, blocked_reason=pre.blocked_reason))
        return out

    # ------------------------------------------------------------------ validate

    async def validate(
        self, user_id: str, spec: ScheduleSpec, *, now: Optional[datetime] = None,
    ) -> ValidatedSchedule:
        now = now or datetime.now(timezone.utc)
        errors: List[FieldError] = []
        is_reminder = spec.capability == REMINDER_CAPABILITY

        fire_at = self._check_timing(spec, is_reminder, now, errors)
        self._check_parameters_size(spec, errors)

        if is_reminder:
            device = await self._check_owned_device(user_id, spec.target_device_id, errors)
            self._raise(errors)
            return ValidatedSchedule(
                capability=REMINDER_CAPABILITY, target_device_id=device, plugin_id=None,
                executor=DEVICE_EXECUTOR, risk="low", requires_consent=False,
                parameters=dict(spec.parameters), fire_at=fire_at)

        if spec.capability.startswith(_NOT_SCHEDULABLE_PREFIXES):
            errors.append(FieldError(field="capability",
                                     message="a schedule cannot create, change or delete schedules or tasks"))
            self._raise(errors)

        index = await self.router.describe_capabilities_with_schema(user_id)
        entry = index.get(spec.capability)
        if entry is None:
            errors.append(FieldError(field="capability", message=(
                f"'{spec.capability}' is not available for your devices and enabled plugins")))
            self._raise(errors)

        target = await self._pick_target(user_id, spec, entry, errors)
        self._check_parameters(spec, entry, errors)
        self._raise(errors)
        assert target is not None

        pre = await self.preflight(
            user_id=user_id, capability=spec.capability, plugin_id=target["plugin_id"],
            device_id=target["device_id"], executor_id=target["executor_id"], parameters=spec.parameters)
        if pre.blocked:
            errors.append(FieldError(field="capability",
                                     message=f"cannot be scheduled: {pre.blocked_reason}"))
            self._raise(errors)
        return ValidatedSchedule(
            capability=spec.capability, target_device_id=target["device_id"],
            plugin_id=target["plugin_id"], executor=target["executor_id"], risk=pre.risk,
            requires_consent=pre.requires_consent, parameters=dict(spec.parameters), fire_at=fire_at)

    # ------------------------------------------------------------------ preflight

    async def preflight(
        self, *, user_id: str, capability: str, plugin_id: str, device_id: str,
        executor_id: str, parameters: Dict[str, Any],
    ) -> Preflight:
        """Dry-run the REAL authorization pipeline as a scheduled run. No audit, no token."""
        if capability in BOUND_PARAMETERS:
            # Approved per command, once or for a session: nobody is there to approve a scheduled run.
            return Preflight(effect="deny", risk="high",
                             reason="it needs your approval every time it runs")
        manifest_risk = self._manifest_risk(plugin_id, capability)
        if executor_id != DEVICE_EXECUTOR:
            # Server plugins and built-ins skip device authorization in TaskManager.run too.
            return Preflight(effect="allow", risk=manifest_risk)
        decision = await self.security.authorize(
            user_id=user_id, plugin_id=plugin_id, capability=capability, device_id=device_id,
            plugin_manifest=None, parameters=parameters,
            scope=ScopeContext(allowed_devices=[device_id]),      # the validated, user-owned target
            origin=Origin.SCHEDULED, approved=False, dry_run=True)
        risk = _max_risk(str(getattr(decision.risk_level, "value", decision.risk_level)), manifest_risk)
        effect = {DecisionEffect.ALLOW: "allow", DecisionEffect.DENY: "deny",
                  DecisionEffect.APPROVAL_REQUIRED: "approval_required"}[decision.effect]
        return Preflight(effect=effect, risk=risk, reason=decision.reason)       # type: ignore[arg-type]

    async def resolve_for_fire(self, row: Any) -> FireTarget:
        """Resolve a due schedule against the CURRENT index (capability/device may have changed)."""
        index = await self.router.describe_capabilities_with_schema(row.user_id)
        entry = index.get(row.capability)
        if entry is None:
            raise ScheduleValidationError([FieldError(
                field="capability", message=f"'{row.capability}' is no longer available")])
        targets = entry.get("targets") or []
        chosen = next((t for t in targets if t["device_id"] == row.target_device_id), None)
        if chosen is None:
            raise ScheduleValidationError([FieldError(
                field="target_device_id", message="the target device no longer provides this capability")])
        pre = await self.preflight(
            user_id=row.user_id, capability=row.capability, plugin_id=chosen["plugin_id"],
            device_id=chosen["device_id"], executor_id=chosen["executor_id"],
            parameters=dict(row.parameters or {}))
        return FireTarget(plugin_id=chosen["plugin_id"], device_id=chosen["device_id"],
                          executor_id=chosen["executor_id"], preflight=pre)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _raise(errors: List[FieldError]) -> None:
        if errors:
            raise ScheduleValidationError(errors)

    def _manifest_risk(self, plugin_id: str, capability: str) -> Risk:
        try:
            manifest = self.security.plugin_manager.get_manifest(plugin_id)
            spec = manifest.get_capability(capability) if manifest else None
            return str(getattr(spec.risk, "value", spec.risk)) if spec else "low"    # type: ignore[return-value]
        except Exception:
            return "low"

    async def _check_owned_device(
        self, user_id: str, device_id: Optional[str], errors: List[FieldError],
    ) -> Optional[str]:
        if not device_id:
            return None
        owned = {getattr(d, "id", None) for d in await self.devices.list_devices(user_id)}
        if device_id not in owned:
            errors.append(FieldError(field="target_device_id", message="unknown device"))
            return None
        return device_id

    async def _pick_target(
        self, user_id: str, spec: ScheduleSpec, entry: Dict[str, Any], errors: List[FieldError],
    ) -> Optional[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = entry.get("targets") or []
        if spec.target_device_id:
            match = next((t for t in targets if t["device_id"] == spec.target_device_id), None)
            if match is None:
                errors.append(FieldError(field="target_device_id", message=(
                    f"that device does not provide '{spec.capability}' (or is not yours)")))
            return match
        for t in targets:                                   # prefer a connected device
            if t["executor_id"] != DEVICE_EXECUTOR or await self.devices.is_connected(t["device_id"]):
                return t
        errors.append(FieldError(field="target_device_id", message=(
            f"no connected device of yours provides '{spec.capability}'")))
        return None

    @staticmethod
    def _check_parameters_size(spec: ScheduleSpec, errors: List[FieldError]) -> None:
        try:
            if len(json.dumps(spec.parameters)) > MAX_PARAMETERS_BYTES:
                errors.append(FieldError(field="parameters", message="parameters are too large"))
        except (TypeError, ValueError):
            errors.append(FieldError(field="parameters", message="parameters must be plain JSON"))

    @staticmethod
    def _check_parameters(spec: ScheduleSpec, entry: Dict[str, Any], errors: List[FieldError]) -> None:
        from pai.planning.goal_decomposer import GoalDecomposer    # the planner's own check
        try:
            GoalDecomposer._validate_params(spec.capability, spec.parameters, entry.get("parameters") or {})
        except ValueError as exc:
            errors.append(FieldError(field="parameters", message=str(exc)))

    @staticmethod
    def _check_timing(
        spec: ScheduleSpec, is_reminder: bool, now: datetime, errors: List[FieldError],
    ) -> Optional[datetime]:
        try:
            tz = ZoneInfo(spec.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(FieldError(field="timezone", message=f"unknown timezone '{spec.timezone}'"))
            tz = ZoneInfo("UTC")
        fire_at: Optional[datetime] = None
        if spec.kind == "one_shot":
            if spec.fire_at is None:
                errors.append(FieldError(field="fire_at", message="required for a one-shot schedule"))
            else:
                fire_at = spec.fire_at if spec.fire_at.tzinfo else spec.fire_at.replace(tzinfo=tz)
                fire_at = fire_at.astimezone(timezone.utc)
                if fire_at <= now:
                    errors.append(FieldError(field="fire_at", message="must be in the future"))
        elif spec.kind == "interval":
            floor = MIN_REMINDER_INTERVAL_SECONDS if is_reminder else MIN_PLUGIN_INTERVAL_SECONDS
            if not spec.interval_seconds or spec.interval_seconds < floor:
                errors.append(FieldError(field="interval_seconds",
                                         message=f"must be at least {floor} seconds"))
        else:   # cron
            expr = (spec.cron or "").strip()
            if not expr or not croniter.is_valid(expr):
                errors.append(FieldError(field="cron", message="not a valid cron expression"))
            elif not is_reminder:
                if len(expr.split()) != 5:
                    errors.append(FieldError(field="cron", message=(
                        "use the 5-field form (minute hour day month weekday); seconds are not allowed")))
                else:
                    itr = croniter(expr, now.astimezone(tz))
                    first, second = itr.get_next(datetime), itr.get_next(datetime)
                    if (second - first) < timedelta(seconds=MIN_PLUGIN_INTERVAL_SECONDS):
                        errors.append(FieldError(field="cron", message=(
                            f"runs more often than every {MIN_PLUGIN_INTERVAL_SECONDS} seconds")))
        return fire_at
