"""Device routes: register, list, heartbeat, and WebSocket."""
from __future__ import annotations

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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import get_app_context, PAIAppContext
from pai.devices.models import DeviceInfo, DevicePlatform, DeviceRegistration, DeviceStatus, DeviceType
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
):
    """
    Register a device.

    If payload.device_id matches an existing device, refresh its
    metadata in place. If it matches a device that exists only in
    the DB (server restarted), restore it into memory with the
    caller-supplied id. Otherwise mint a new id.
    """
    try:
        device_manager = context.orchestrator.device_manager

        client = request.client.host if request.client else "unknown"
        logger.info(
            "Device registration from %s: name=%s existing_id=%s",
            client,
            payload.device.name,
            payload.device_id,
        )

        # ---------- Reuse existing (in-memory) device ----------
        if payload.device_id:
            existing = await device_manager.get(payload.device_id)

            if existing is not None:
                update = {
                    "name": payload.device.name or existing.name,
                    "platform": payload.device.platform or existing.platform,
                    "architecture": payload.device.architecture or existing.architecture,
                    "device_type": payload.device.device_type or existing.device_type,
                    "platform_version": payload.device.platform_version or existing.platform_version,
                    "os_version": payload.device.os_version or existing.os_version,
                    "hostname": payload.device.hostname or existing.hostname,
                    "app_version": payload.device.app_version or existing.app_version,
                    "runtime_version": payload.device.runtime_version or existing.runtime_version,
                }

                try:
                    await device_manager.refresh_metadata(
                        payload.device_id, update, user_id=user_id,
                    )
                except AttributeError:
                    logger.debug(
                        "device_manager.refresh_metadata not available; "
                        "skipping refresh"
                    )

                logger.info(
                    "Device re-registered: %s (%s)",
                    payload.device_id,
                    update["name"],
                )

                return {
                    "success": True,
                    "device_id": payload.device_id,
                    "device": existing.model_dump() if hasattr(existing, "model_dump") else existing,
                    "message": "Device re-registered",
                }

            # ---------- Not in memory — restore into memory with the caller's id ----------
            logger.info(
                "Existing device id %s not in memory — restoring with caller's id",
                payload.device_id,
            )

            device = await device_manager.register(
                DeviceInfo(
                    id=payload.device_id,
                    name=payload.device.name,
                    device_type=payload.device.device_type or DeviceType.UNKNOWN,
                    status=DeviceStatus.OFFLINE,
                    platform=payload.device.platform or DevicePlatform.UNKNOWN,
                    platform_version=payload.device.platform_version or "",
                    os_version=payload.device.os_version or "",
                    architecture=payload.device.architecture or "",
                    hostname=payload.device.hostname or "",
                    app_version=payload.device.app_version or "",
                    runtime_version=payload.device.runtime_version or "",
                    capabilities=list(payload.device.capabilities or []),
                    user_id=user_id,       # ← carry ownership through
                ),
                user_id=user_id,
            )

            logger.info("Device restored: %s (%s)", device.id, device.name)

            return {
                "success": True,
                "device_id": device.id,
                "device": device.model_dump() if hasattr(device, "model_dump") else device,
                "message": "Device restored",
            }

        # ---------- Mint a new device ----------
        device = await device_manager.register(payload, user_id=user_id)

        logger.info("Device registered: %s (%s)", device.id, device.name)

        # Best-effort ownership attach (in case register() didn't persist it).
        try:
            await device_manager.assign_user(device.id, user_id)
        except AttributeError:
            logger.debug(
                "device_manager.assign_user not available; "
                "user_id will not be stored on the device row"
            )

        return {
            "success": True,
            "device_id": device.id,
            "device": device.model_dump() if hasattr(device, "model_dump") else device,
            "message": "Device registered",
        }

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
    return {"devices": devices}


# devices.py — additions

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

    return {"ok": True, "device_id": device_id, "capability": payload.capability, "result": result}

# ============================================================
# Device WebSocket
# ============================================================

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

    if token:
        try:
            from pai.security.auth import decode_access_token
            user_id = decode_access_token(token)
        except Exception as exc:
            logger.warning("WS token decode failed: %s", exc)
            user_id = None

    if user_id is None:
        logger.warning("Device WS rejected: missing/invalid token")
        await websocket.close(code=4401)
        return

    context = websocket.app.state.context
    device_manager = context.orchestrator.device_manager

    logger.info(
        "Device WS connect: device=%s user=%s", device_id, user_id,
    )

    # ---------- 2. Look up the device ----------
    device = await device_manager.get(device_id)

    # ---------- 3. Auto-register if the device is missing ----------
    if device is None:
        logger.info(
            "Device %s not in registry — auto-registering for user %s",
            device_id, user_id,
        )
        try:
            device = await device_manager.register(
                DeviceInfo(
                    id=device_id,
                    name=device_id,
                    device_type=DeviceType.UNKNOWN,
                    status=DeviceStatus.ONLINE,
                    platform=DevicePlatform.UNKNOWN,
                    platform_version="",
                    os_version="",
                    architecture="",
                    hostname="",
                    app_version="",
                    runtime_version="",
                    capabilities=[],
                    user_id=user_id,
                ),
                user_id=user_id,
            )
            # Ensure ownership is persisted for future restarts.
            try:
                await device_manager.assign_user(device_id, user_id)
            except Exception:
                pass
            logger.info("Device %s auto-registered", device_id)
        except Exception as exc:
            logger.exception(
                "Auto-register failed for %s: %s", device_id, exc,
            )
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "code": "device_register_failed",
                "message": str(exc),
            })
            await websocket.close(code=4500)
            return

    print(device,user_id)
    # ---------- 4. Ownership check ----------
    if getattr(device, "user_id", None) and device.user_id != user_id:
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

        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            logger.debug("Device message [%s]: %s", device_id, message)

            if message_type == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "device_id": device_id,
                })

            elif message_type == "heartbeat":
                await device_manager.registry.heartbeat(device_id)
                await websocket.send_json({
                    "type": "heartbeat_ack",
                    "device_id": device_id,
                })

            elif message_type == "result":
                request_id = message.get("request_id")
                logger.info("Result from %s: request_id=%s", device_id, request_id)

                executor_manager = context.orchestrator.executor_manager
                device_executor = executor_manager.get("device_executor")
                if device_executor is not None and hasattr(device_executor, "deliver_result"):
                    device_executor.deliver_result(request_id, message.get("result") or message)
            elif message_type == "event":
                logger.info("Event from %s: %s", device_id, message)

            else:
                logger.warning(
                    "Unknown device message type [%s]: %s",
                    device_id, message_type,
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