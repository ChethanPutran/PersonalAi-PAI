import base64
import logging

from typing import Optional, Dict, Any, List

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from pydantic import BaseModel

from pai.app_context import (
    get_app_context,
    PAIAppContext,
)

from pai.devices.models import (
    DeviceRegistration,
    DeviceStatus,
)

from pai.devices.websocket import (
    WebSocketDeviceConnection,
)


router = APIRouter(
    prefix="/devices",
    tags=["devices"],
)

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


# ============================================================
# Device Registration
# ============================================================

@router.post("/register")
async def register_device(
    request: Request,
    device_registration: DeviceRegistration,
    context: PAIAppContext = Depends(get_app_context),
):
    try:
        client = (
            request.client.host
            if request.client
            else "unknown"
        )

        logger.info(
            "Incoming device registration from %s: %s",
            client,
            device_registration.device.name,
        )

        device = await (
            context
            .orchestrator
            .device_manager
            .register(device_registration)
        )

        logger.info(
            "Device registered: %s (%s)",
            device.id,
            device.name,
        )

        return {
            "success": True,
            "device_id": device.id,
            "device": device.model_dump(),
            "message": "Device registered",
        }

    except Exception as exc:
        logger.exception(
            "Device registration failed"
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to register device",
        ) from exc


# ============================================================
# List Devices
# ============================================================

@router.get("/list/{user_id}")
async def list_devices(
    user_id: str,
    context: PAIAppContext = Depends(get_app_context),
):
    devices = (
        await context
        .orchestrator
        .device_manager
        .list_devices(user_id)
    )

    logger.info(
        "Devices returned for user %s: %d",
        user_id,
        len(devices),
    )

    return {
        "devices": devices,
    }


# ============================================================
# Heartbeat
# ============================================================

@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: str,
    payload: Dict[str, Any],
    context: PAIAppContext = Depends(get_app_context),
):
    result = (
        context
        .orchestrator
        .device_manager
        .update_status(
            device_id,
            status=DeviceStatus.ONLINE,
        )
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="device not found",
        )

    return {
        "status": "ok",
    }


# ============================================================
# Device WebSocket
# ============================================================

@router.websocket("/{device_id}/ws")
async def device_websocket(
    websocket: WebSocket,
    device_id: str,
):
    """
    Persistent WebSocket connection for a device.

    Final endpoint:

        /api/v1/devices/{device_id}/ws
    """

    # --------------------------------------------------------
    # IMPORTANT:
    # main.py stores the context as app.state.context
    # --------------------------------------------------------

    context = websocket.app.state.context

    device_manager = (
        context
        .orchestrator
        .device_manager
    )

    logger.info(
        "Device WebSocket connection request: %s",
        device_id,
    )

    # --------------------------------------------------------
    # Accept handshake FIRST
    # --------------------------------------------------------

    await websocket.accept()

    logger.info(
        "Device WebSocket accepted: %s",
        device_id,
    )

    # --------------------------------------------------------
    # Verify device exists
    # --------------------------------------------------------

    try:
        device = await device_manager.require(
            device_id
        )

    except Exception as exc:
        logger.warning(
            "Device WebSocket rejected: %s - %s",
            device_id,
            exc,
        )

        await websocket.close(
            code=4004,
            reason="Device not registered",
        )

        return

    logger.info(
        "Device verified: %s (%s)",
        device_id,
        device.name,
    )

    # --------------------------------------------------------
    # Create connection wrapper
    # --------------------------------------------------------

    connection = WebSocketDeviceConnection(
        device_id=device_id,
        websocket=websocket,
    )

    try:

        # This should NOT call websocket.accept().
        await connection.connect()

        # Register connection with DeviceManager.
        await device_manager.attach_connection(
            device_id,
            connection,
        )

        logger.info(
            "Device connection attached: %s",
            device_id,
        )

        # ----------------------------------------------------
        # Tell Flutter that connection is established
        # ----------------------------------------------------

        await websocket.send_json({
            "type": "connected",
            "device_id": device_id,
        })

        # ----------------------------------------------------
        # Receive messages
        # ----------------------------------------------------

        while True:

            message = (
                await websocket.receive_json()
            )

            message_type = message.get("type")

            logger.debug(
                "Device message [%s]: %s",
                device_id,
                message,
            )

            if message_type == "ping":

                await websocket.send_json({
                    "type": "pong",
                    "device_id": device_id,
                })

            elif message_type == "heartbeat":

                await (
                    device_manager
                    .registry
                    .heartbeat(device_id)
                )

                await websocket.send_json({
                    "type": "heartbeat_ack",
                    "device_id": device_id,
                })

            elif message_type == "result":

                logger.info(
                    "Result received from %s: %s",
                    device_id,
                    message,
                )

                # TODO:
                # Forward result to TaskRunner /
                # Orchestrator.

            elif message_type == "event":

                logger.info(
                    "Event received from %s: %s",
                    device_id,
                    message,
                )

                # TODO:
                # Publish event to event bus.

            else:

                logger.warning(
                    "Unknown device message type [%s]: %s",
                    device_id,
                    message_type,
                )

    except WebSocketDisconnect:

        logger.info(
            "Device WebSocket disconnected: %s",
            device_id,
        )

    except Exception:

        logger.exception(
            "WebSocket error for device %s",
            device_id,
        )

    finally:

        try:
            await device_manager.detach_connection(
                device_id
            )
        except Exception:

            logger.exception(
                "Failed to detach device connection: %s",
                device_id,
            )

        logger.info(
            "Device WebSocket cleanup complete: %s",
            device_id,
        )


# ============================================================
# Permission WebSocket
# ============================================================

@router.websocket("/ws/permissions")
async def permission_ws(
    websocket: WebSocket,
):
    await websocket.accept()

    logger.info(
        "Permission WebSocket connected"
    )

    try:

        while True:

            message = (
                await websocket.receive_json()
            )

            logger.info(
                "Permission message: %s",
                message,
            )

    except WebSocketDisconnect:

        logger.info(
            "Permission WebSocket disconnected"
        )


# ============================================================
# Translation WebSocket
# ============================================================

@router.websocket("/ws/translate")
async def live_translate(
    websocket: WebSocket,
):
    await websocket.accept()

    logger.info(
        "Translation WebSocket connected"
    )

    audio_buffer = b""
    target_lang = "es"

    try:

        while True:

            data = (
                await websocket.receive_json()
            )

            if data.get("type") == "audio":

                audio_buffer += base64.b64decode(
                    data["data"]
                )

                # TODO:
                # STT -> translation -> TTS

    except WebSocketDisconnect:

        logger.info(
            "Translation WebSocket disconnected"
        )