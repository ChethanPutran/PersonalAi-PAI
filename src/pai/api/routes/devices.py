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

from pai.api.dependencies import get_current_user, get_db
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


@router.post("/{device_id}/invoke")
async def invoke_on_device(
    device_id: str,
    payload: InvokeRequest,
    context: PAIAppContext = Depends(get_app_context),
    user_id: str = Depends(get_current_user),
):
    """
    Run a capability on a device and wait for its result.
    Synchronous: blocks until the device replies or `timeout_seconds` elapses.
    """
    dm = context.orchestrator.device_manager

    device = await dm.get(device_id)
    if device is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    
    if getattr(device, "user_id", None) and device.user_id != user_id:
        raise HTTPException(403, "Device belongs to a different user")

    if not await dm.is_connected(device_id):
        raise HTTPException(409, f"Device '{device_id}' is not connected")

    executor_manager = context.orchestrator.executor_manager
    device_executor = executor_manager.get("device_executor")
    if device_executor is None or not hasattr(device_executor, "invoke"):
        raise HTTPException(
            500, "device_executor with invoke() not available"
        )

    import asyncio
    try:
        result = await device_executor.invoke(
            device_id=device_id,
            capability=payload.capability,
            parameters=payload.parameters,
            timeout=payload.timeout_seconds,
        )
        logger.info(
            "Invoke result from %s: capability=%s result=%s",
            device_id, payload.capability, result,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"Device did not reply within {payload.timeout_seconds}s",
        ) from None
    except RuntimeError as exc:
        # DM's send() raises RuntimeError when the socket disappears
        # between the is_connected check and the actual send.
        raise HTTPException(409, str(exc)) from exc

    return JSONResponse(
        content={
            "ok": True,
            "device_id": device_id,
            "capability": payload.capability,
            "result": result,
        },
        # charset=utf-8 tells Dart http to decode as UTF-8, not Latin-1.
        media_type="application/json; charset=utf-8",
    )


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

        await websocket.send_json({
            "type": "connected",
            "device_id": device_id,
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