from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pai.api.dependencies import get_current_user, get_db
from pai.app_context import get_app_context, PAIAppContext
from pai.devices.models import DeviceRegistration, DeviceStatus
from pai.devices.websocket import WebSocketDeviceConnection


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
    device_id: Optional[str] = None     # ← existing id, when re-registering


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
    metadata in place. Otherwise mint a new one.

    The owning user comes from the JWT — the device is tied to
    whoever is signed in on the app.
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

        # ---------- Reuse existing device ----------
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

            logger.info(
                "Existing device id %s not found — minting a new one",
                payload.device_id,
            )

        # ---------- Mint a new device ----------
        device = await device_manager.register(payload)

        logger.info("Device registered: %s (%s)", device.id, device.name)

        # Attach the owning user (best-effort).
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
):
    devices = await context.orchestrator.device_manager.list_devices(user_id)
    logger.info("Devices returned for user %s: %d", user_id, len(devices))
    return {"devices": devices}


# ============================================================
# Heartbeat
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


# ============================================================
# Device WebSocket
# ============================================================

@router.websocket("/{device_id}/ws")
async def device_websocket(websocket: WebSocket, device_id: str):
    """
    Persistent WebSocket connection for a device.

    Final endpoint: /api/v1/devices/{device_id}/ws?token=<jwt>
    """
    # Optional: validate token from query string.
    token = websocket.query_params.get("token")
    if token:
        from pai.security.auth import decode_access_token
        user_id = decode_access_token(token)
        if user_id is None:
            await websocket.close(code=4401)
            return

    context = websocket.app.state.context
    device_manager = context.orchestrator.device_manager

    logger.info("Device WebSocket connection request: %s", device_id)
    await websocket.accept()

    try:
        device = await device_manager.require(device_id)
    except Exception as exc:
        logger.warning("Device WebSocket rejected: %s - %s", device_id, exc)
        await websocket.close(code=4004, reason="Device not registered")
        return

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
                logger.info("Result from %s: %s", device_id, message)

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