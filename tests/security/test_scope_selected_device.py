"""The device the server selected is trusted for device-scoped permissions (camera etc.)."""
from pai.common.enums.permission import Origin, PermissionAction, PermissionResource, PrincipalType
from pai.orchestration.execution_context import ExecutionContext
from pai.security.authorization.context import ScopeContext
from pai.security.authorization.request import Actor, AuthorizationRequest, ResourceRef
from pai.security.scopes.device import DeviceScope


def _allowed(scope: ScopeContext) -> bool:
    req = AuthorizationRequest(
        subject=Actor(PrincipalType.PLUGIN, "camera"), action=PermissionAction.CONTROL,
        resource=ResourceRef(PermissionResource.CAMERA, None), target_device_id="phone",
        context=scope.model_dump(), origin=Origin.USER_DIRECT)
    return DeviceScope.evaluate(req, None).allowed


def test_a_chat_context_alone_has_no_allowed_devices_but_the_selected_target_is_added():
    base = ScopeContext.from_execution_context(
        ExecutionContext(user_id="u", session_id="s", metadata={"mode": "text"}))
    assert base.allowed_devices == [] and not _allowed(base)          # the bug
    assert _allowed(base.with_device("phone"))                          # the fix


def test_with_device_adds_only_the_selected_device_and_never_widens_to_others():
    scope = ScopeContext(allowed_devices=["tablet"]).with_device("phone")
    assert scope.allowed_devices == ["tablet", "phone"]
    assert scope.with_device("phone") is scope and scope.with_device(None) is scope
    assert not _allowed(ScopeContext().with_device("tablet"))           # target is "phone", not "tablet"
