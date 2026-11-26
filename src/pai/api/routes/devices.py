"""Device routes: register, list, heartbeat, and WebSocket."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from uuid import uuid4

from pai.api.dependencies import get_current_user, get_db, get_source_device_id, user_is_admin
from pai.common.enums.permission import Origin
from pai.orchestration.capability_router import CapabilityNotAvailable
from pai.orchestration.execution_context import ExecutionContext
from pai.planning.models import TaskSpec
from pai.security.audit.models import AuditEventType
from pai.security.consent.models import ConfirmationInfo
from pai.tasks.models import TaskStatus
from pai.app_context import get_app_context, PAIAppContext
from pai.common.enums.device import DeviceArchitecture, DeviceMessageType
from pai.devices.manager import DeviceManager
from pai.devices.models import DeviceFrame, DeviceInfo, DevicePlatform, DeviceRegistration, DeviceRegistrationInfo, DeviceRegistrationResponse, DeviceStatus, DeviceType
from pai.devices.websocket import WebSocketDeviceConnection
from pydantic import BaseModel
from fastapi import Body

router = APIRouter(prefix="/devices", tags=["devices"])
logger = logging.getLogger(__name__)


# ============================================================
# Models
# ============================================================

class DevicePlugin(BaseModel):
    id: str
    version: str
    enabled: bool
    capabilities: List[str]


class PluginSyncRequest(BaseModel):
    plugins: List[DevicePlugin]


class RegisterRequest(BaseModel):
    device: Dict[str, Any]
    device_id: Optional[str] = None     # existing id, when re-registering


# ============================================================
# Register
# ============================================================

@router.post("/register")
async def register_device(
    request: Request,
    payload: DeviceRegistration,
    user_id: str = Depends(get_current_user),
    context: PAIAppContext = Depends(get_app_context),
)-> DeviceRegistrationResponse:
    """
    Register a device.

    If payload.device_id matches an existing device, refresh its
    metadata in place. If it matches a device that exists only in
    the DB (server restarted), restore it into memory with the
    caller-supplied id. Otherwise mint a new id.
    """
    try:
        client = request.client.host if request.client else "unknown"
        logger.info(
            "Device registration from %s: name=%s existing_id=%s",
            client,
            payload.device.name,
            payload.device_id,
        )
        
        device_manager = context.orchestrator.device_manager

        device = await device_manager.register(
            registration=payload,
            user_id=user_id
        )
        return DeviceRegistrationResponse(
            success=True,
            device_id=device.id,
            device=device,
            message="Device registered",
        )

    except Exception as exc:
        logger.exception("Device registration failed")
        raise HTTPException(
            status_code=500,
            detail="Failed to register device",
        ) from exc


# ============================================================
# List devices
# ============================================================

@router.get("/list/{user_id}")
async def list_devices(
    user_id: str,
    context: PAIAppContext = Depends(get_app_context),
    current_user: str = Depends(get_current_user),
):
    if user_id != current_user:
        raise HTTPException(403, "You can only list your own devices")
    devices = await context.orchestrator.device_manager.list_devices(user_id)
    return devices



@router.post("/{device_id}/disconnect")
async def disconnect_my_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
):
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    if getattr(device, "user_id", None) != user_id:
        raise HTTPException(403, "Not your device")
    await dm.disconnect(device_id)
    return {"device_id": device_id, "disconnected": True}


@router.delete("/{device_id}", status_code=204)
async def remove_my_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
):
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    if getattr(device, "user_id", None) != user_id:
        raise HTTPException(403, "Not your device")
    removed = await dm.unregister(device_id)
    if not removed:
        raise HTTPException(404, "Device not found")
    return Response(status_code=204)

# ============================================================
# Remote access (what other devices may do to this one)
# ============================================================

class RemoteAccessUpdate(BaseModel):
    """Send only what changes (``None`` = leave as is)."""
    allow_remote_install: Optional[bool] = None
    allow_remote_invoke: Optional[bool] = None


def _remote_access_view(device: Any) -> Dict[str, Any]:
    return {
        "device_id": device.id,
        "name": device.name,
        "allow_remote_install": bool(getattr(device, "allow_remote_install", False)),
        "allow_remote_invoke": bool(getattr(device, "allow_remote_invoke", False)),
    }


@router.get("/{device_id}/remote-access")
async def get_remote_access(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    device = await context.orchestrator.device_manager.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, "Device not found")
    return _remote_access_view(device)


@router.put("/{device_id}/remote-access")
async def set_remote_access(
    device_id: str,
    payload: RemoteAccessUpdate,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Turn remote plugin install / remote plugin use on or off for ONE of your devices.

    Both are off by default. While off, nothing outside the device (another of your devices, the
    web, a schedule, an agent) can install plugins on it or run plugins on it. Owner only;
    every change is audited.
    """
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, "Device not found")
    before = _remote_access_view(device)
    updated = await dm.set_remote_access(
        device_id, install=payload.allow_remote_install, invoke=payload.allow_remote_invoke)
    if updated is None:
        raise HTTPException(404, "Device not found")
    after = _remote_access_view(updated)
    context.orchestrator.security_manager.audit_logger.record(
        AuditEventType.DEVICE_REMOTE_ACCESS_CHANGED, user_id=user_id, device_id=device_id,
        allowed=True, reason="remote access settings changed",
        metadata={"before": {k: before[k] for k in ("allow_remote_install", "allow_remote_invoke")},
                  "after": {k: after[k] for k in ("allow_remote_install", "allow_remote_invoke")}})
    return after


# ============================================================
# Heartbeat (HTTP)
# ============================================================

@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str,
    payload: Dict[str, Any],
    context: PAIAppContext = Depends(get_app_context),
):
    logger.info("Heartbeat from device %s: %s", device_id, payload)
    result = await context.orchestrator.device_manager.update_status(
        device_id, status=DeviceStatus.ONLINE,
    )
    if not result:
        raise HTTPException(status_code=404, detail="device not found")
    return {"status": "ok"}



class InvokeRequest(BaseModel):
    capability: str
    parameters: Dict[str, Any] = {}
    timeout_seconds: float = 60.0
    session_id: Optional[str] = None      # default "tester:<user_id>"
    wait_seconds: float = 15.0            # how long to wait for the result


class InvokeResponse(BaseModel):
    """Result of the plugin tester's run. It is a REAL task (visible in Tasks, audited)."""
    task_id: str
    status: str
    ok: bool = False
    device_id: str
    capability: str
    result: Optional[Any] = None
    error: Optional[str] = None
    awaiting_confirmation: bool = False
    confirmation: Optional[ConfirmationInfo] = None


def _jsonable(value: Any) -> Any:
    """Make an executor result JSON-safe (it may hold pydantic models / frames / bytes)."""
    from fastapi.encoders import jsonable_encoder
    try:
        return jsonable_encoder(value, custom_encoder={bytes: lambda b: base64.b64encode(b).decode()})
    except Exception:  # noqa: BLE001 — never 500 on an odd result shape
        return str(value)


def _invoke_response(task: Any, device_id: str, capability: str) -> InvokeResponse:
    awaiting = task.status == TaskStatus.PAUSED and task.confirmation is not None
    res = task.result
    out = None
    if res is not None:
        out = _jsonable(res.output if res.output is not None else res.model_dump(mode="json"))
    return InvokeResponse(
        task_id=task.id, status=task.status.value, device_id=device_id, capability=capability,
        ok=task.status == TaskStatus.COMPLETED and (res is None or bool(res.success)),
        result=out, error=task.error or (res.error if res is not None else None),
        awaiting_confirmation=awaiting, confirmation=task.confirmation if awaiting else None)


@router.post("/{device_id}/invoke", response_model=InvokeResponse)
async def invoke_on_device(
    device_id: str,
    payload: InvokeRequest,
    response: Response,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    source_device_id: Optional[str] = Depends(get_source_device_id),
):
    """Run a capability on a device THROUGH THE REAL PIPELINE (the plugin tester).

    Same path as every other task: resolve -> authorize (policies, risk, audit) -> task record
    -> confirmation gate -> execute. The only special case: an ADMIN (decided here from the DB)
    skips the confirmation prompt; policy DENY still blocks them. A non-admin whose action needs
    confirmation gets HTTP 202 with ``awaiting_confirmation`` and the ``confirmation`` details;
    they approve via ``POST /tasks/{task_id}/confirm`` and poll/stream the task.
    """
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    if getattr(device, "user_id", None) and device.user_id != user_id:
        raise HTTPException(403, "Device belongs to a different user")
    if not await dm.is_connected(device_id):
        raise HTTPException(409, f"Device '{device_id}' is not connected")

    is_admin = await user_is_admin(session, user_id)
    exec_ctx = ExecutionContext(
        user_id=user_id,
        session_id=payload.session_id or f"tester:{user_id}",
        origin=Origin.USER_DIRECT,
        pre_approved=is_admin,            # set by the server only; never from the request
        device_id=device_id,
        source_device_id=source_device_id,      # the target's "allow remote use" switch applies unless == device_id
        # Trusted scope: the device was just checked to be this user's own and online.
        metadata={"allowed_devices": [device_id]},
    )
    spec = TaskSpec(
        id=f"tester_{uuid4().hex[:12]}", name=payload.capability, capability=payload.capability,
        parameters=dict(payload.parameters), preferred_device=device_id,
        timeout=payload.timeout_seconds, retry_count=0,
        metadata={"source": "plugin_tester"},
    )
    tm = context.orchestrator.task_manager
    try:
        await tm.run(spec, exec_ctx, wait=False)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CapabilityNotAvailable as exc:
        raise HTTPException(409, detail={"code": "capability_unavailable",
                                         "message": str(exc.error.reason)}) from exc
    task = await tm.find_by_idempotency_key(
        f"{exec_ctx.request_id}:{spec.id}", user_id=user_id, session_id=exec_ctx.session_id)
    if task is None:
        raise HTTPException(500, "Task was not created")

    # Wait for the result — but not for a human: a pending confirmation returns at once.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, min(payload.wait_seconds, payload.timeout_seconds, 120.0))
    while True:
        task = await tm.get(task.id)
        if task.terminal or (task.status == TaskStatus.PAUSED and task.confirmation is not None):
            break
        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.1)

    out = _invoke_response(task, device_id, payload.capability)
    if out.awaiting_confirmation:
        response.status_code = 202
    return out


@router.post("/{device_id}/trust")
async def trust_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
) -> Dict[str, Any]:
    dm = context.orchestrator.device_manager
    device = await dm.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, "Device not found")
    await context.orchestrator.plugin_guard.trust_device(user_id, device_id)
    return {"device_id": device_id, "trusted": True}


@router.delete("/{device_id}/trust", status_code=204)
async def untrust_device(
    device_id: str,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
):
    await context.orchestrator.plugin_guard.untrust_device(user_id, device_id)
    return Response(status_code=204)

# ============================================================
# Device WebSocket
# ============================================================

async def dispatch_device_frame(
    frame: DeviceFrame,
    *,
    device_id: str,
    context: PAIAppContext,
    websocket: WebSocket,
) -> None:
    """Handle one frame received from a device (device → backend)."""
    device_manager = context.orchestrator.device_manager
    ftype = frame.type
    logger.debug("Device frame [%s]: %s", device_id, ftype)

    if ftype == DeviceMessageType.PING.value:
        await websocket.send_json({"type": "pong", "device_id": device_id})

    elif ftype == DeviceMessageType.HEARTBEAT.value:
        await device_manager.registry.heartbeat(device_id)
        await device_manager.mark_seen(device_id)
        await websocket.send_json({"type": "heartbeat_ack", "device_id": device_id})

    elif ftype == DeviceMessageType.RESULT.value:
        logger.info("Result from %s: request_id=%s", device_id, frame.request_id)
        device_executor = context.orchestrator.executor_manager.get("device_executor")
        if device_executor is not None and frame.request_id:
            device_executor.deliver_result(frame.request_id, frame)

    elif ftype == DeviceMessageType.CONTROL_ACK.value:
        device_executor = context.orchestrator.executor_manager.get("device_executor")
        if device_executor is None or not device_executor.handle_control_ack(frame):
            logger.debug("Unmatched control_ack from %s: %s", device_id, frame.request_id)

    elif ftype in (DeviceMessageType.STREAM_START.value,
                   DeviceMessageType.STREAME_FRAME.value,
                   DeviceMessageType.STREAM_END.value):
        await context.orchestrator.task_manager.on_stream_frame(frame)
        if ftype == DeviceMessageType.STREAM_END.value:
            # End of stream completes the (otherwise open-ended) task.
            device_executor = context.orchestrator.executor_manager.get("device_executor")
            if device_executor is not None and frame.request_id:
                device_executor.deliver_result(frame.request_id, frame)

    elif ftype == DeviceMessageType.PLUGIN_PROGRESS.value:
        # A device reports how far a plugin install/uninstall got. Only its own operations count
        # (PluginOperations checks the operation belongs to THIS device).
        extra = frame.model_extra or {}
        op_id = extra.get("operation_id")
        ops = context.orchestrator.plugin_manager.operations
        if op_id and ops is not None:
            await ops.progress(
                str(op_id), device_id=device_id, status=str(frame.status or ""),
                error=frame.error, error_code=extra.get("error_code"), version=extra.get("version"))
        else:
            logger.debug("plugin.progress without an operation id from %s", device_id)

    elif ftype == DeviceMessageType.EVENT.value:
        logger.info("Event from %s: %s", device_id, frame.model_dump_json())

    else:
        logger.warning("Unknown device message type [%s]: %s", device_id, ftype)


@router.websocket("/{device_id}/ws")
async def device_websocket(websocket: WebSocket, device_id: str):
    """
    Persistent WebSocket for a device.

    Endpoint: /api/v1/devices/{device_id}/ws?token=<jwt>

    Behaviour on connect:
      1. JWT in query string is decoded → user_id. Missing/invalid → close 4401.
      2. If the device is known and owned by another user → close 4003.
      3. If the device is missing (fresh restart, DB wipe) but the JWT is
         valid → auto-register it for that user and proceed. This makes
         server restarts invisible to the client — the next reconnect just
         works.
    """
    # ---------- 1. Authenticate ----------
    token = websocket.query_params.get("token")
    user_id: Optional[str] = None

    if not token:
        logger.warning("Device WS: no token for device %s", device_id)
        # Accept first so the client receives a close code instead of a
        # bare 403 upgrade rejection. Without this, dart:io surfaces a
        # generic WebSocketException and the client retries forever.
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "missing_token",
            "message": "Authentication token required",
        })
        await websocket.close(code=4401)
        return

    try:
        from pai.security.tokens.auth import decode_access_token
        user_id = decode_access_token(token)
    except Exception as exc:
        # Log the actual reason + a safe token fingerprint so the next
        # 403 is diagnosable from the server log alone.
        logger.warning(
            "WS token decode failed for device %s: %s "
            "(token_len=%d, head=%r, tail=%r)",
            device_id, exc, len(token), token[:8], token[-8:],
        )
        user_id = None


    if user_id is None:
        logger.warning(
            "Device WS: invalid/expired token for device %s "
            "(token_len=%d, tail=%r)",
            device_id, len(token), token[-8:],
        )
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "invalid_token",
            "message": "Invalid or expired token",
        })
        await websocket.close(code=4401)
        return

    logger.info(
        "Device WS auth OK: device=%s user=%s", device_id, user_id,
    )

    context = websocket.app.state.context
    device_manager: DeviceManager = context.orchestrator.device_manager

    logger.info(
        "Device WS connect: device=%s user=%s", device_id, user_id,
    )

    # ---------- 2. Look up the device ----------
    device = await device_manager.get(device_id)

    # ---------- 3. Auto-register if the device is missing ----------
    if device is None:
        logger.error(
            "Device %s not in registry — auto-registering for user %s",
            device_id, user_id,
        )
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "device_register_failed",
            "message": "Device not found",
        })
        await websocket.close(code=4500)
        return

    # ---------- 4. Ownership check ----------
    if device.user_id is not None and device.user_id != user_id:
        logger.warning(
            "Device WS ownership mismatch: device=%s owner=%s caller=%s",
            device_id, device.user_id, user_id,
        )
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "code": "device_not_owned",
            "message": "Device belongs to a different user",
        })
        await websocket.close(code=4003)
        return

    # ---------- 5. Accept and attach ----------
    await websocket.accept()
    logger.info("Device verified: %s (%s)", device_id, device.name)

    connection = WebSocketDeviceConnection(
        device_id=device_id,
        websocket=websocket,
    )

    try:
        await connection.connect()
        await device_manager.attach_connection(device_id, connection)
        logger.info("Device connection attached: %s", device_id)

        me = await device_manager.get(device_id)
        await websocket.send_json({
            "type": "connected",
            "device_id": device_id,
            # What the owner allows others to do to this device (the device enforces it too).
            "remote_access": {
                "install": bool(getattr(me, "allow_remote_install", False)),
                "invoke": bool(getattr(me, "allow_remote_invoke", False)),
            },
        })

        HEARTBEAT_TIMEOUT_SECONDS = 90.0   # 3× client heartbeat interval

        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Device %s: no frame in %.0fs — closing stale socket",
                    device_id, HEARTBEAT_TIMEOUT_SECONDS,
                )
                break                       # ← exits the OUTER loop → finally runs
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                logger.warning("Bad frame from %s: %s", device_id, exc)
                continue

            try:
                frame = DeviceFrame.model_validate(message)
            except Exception as exc:
                logger.warning("Malformed frame from %s: %s", device_id, exc)
                continue
            await dispatch_device_frame(
                frame, device_id=device_id, context=context, websocket=websocket,
            )

    except WebSocketDisconnect:
        logger.info("Device WebSocket disconnected: %s", device_id)

    except Exception:
        logger.exception("WebSocket error for device %s", device_id)

    finally:
        try:
            await device_manager.detach_connection(device_id)
        except Exception:
            logger.exception("Failed to detach device connection: %s", device_id)

        logger.info("Device WebSocket cleanup complete: %s", device_id)


# ============================================================
# Permission / translation WebSockets (unchanged)
# ============================================================

@router.websocket("/ws/permissions")
async def permission_ws(websocket: WebSocket):
    await websocket.accept()
    logger.info("Permission WebSocket connected")
    try:
        while True:
            message = await websocket.receive_json()
            logger.info("Permission message: %s", message)
    except WebSocketDisconnect:
        logger.info("Permission WebSocket disconnected")


@router.websocket("/ws/translate")
async def live_translate(websocket: WebSocket):
    await websocket.accept()
    logger.info("Translation WebSocket connected")
    audio_buffer = b""
    target_lang = "es"
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "audio":
                audio_buffer += base64.b64decode(data["data"])
    except WebSocketDisconnect:
        logger.info("Translation WebSocket disconnected")