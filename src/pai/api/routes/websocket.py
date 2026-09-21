import base64
from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

from loguru import logger

from pai.devices.websocket import (
    WebSocketDeviceConnection,
)

router = APIRouter()

@router.websocket(
    "/{device_id}/ws"
)
async def device_websocket(
    websocket: WebSocket,
    device_id: str,
):
    """
    Persistent WebSocket connection for a device.
    """

    context = websocket.app.state.context

    device_manager = (
        context.orchestrator.device_manager
    )

    # Make sure the device was registered first.
    try:
        await device_manager.require(
            device_id
        )
    except Exception:
        await websocket.close(
            code=1008
        )
        return

    connection = WebSocketDeviceConnection(
        device_id=device_id,
        websocket=websocket,
    )

    try:
        # Accept the WebSocket.
        await connection.connect()

        # Give DeviceManager ownership of it.
        await device_manager.attach_connection(
            device_id,
            connection,
        )

        # Receive messages from Flutter.
        while True:
            message = (
                await websocket.receive_json()
            )

            message_type = message.get(
                "type"
            )

            if message_type == "ping":

                await websocket.send_json({
                    "type": "pong",
                    "device_id": device_id,
                })

            elif message_type == "heartbeat":

                await device_manager.registry.heartbeat(
                    device_id
                )

                await websocket.send_json({
                    "type": "heartbeat_ack",
                    "device_id": device_id,
                })

            elif message_type == "result":

                logger.info(
                    "Result received from {}: {}",
                    device_id,
                    message,
                )

                # Later:
                # task_manager.handle_result(...)
                # event_bus.publish(...)

            elif message_type == "event":

                logger.info(
                    "Event received from {}: {}",
                    device_id,
                    message,
                )

                # Later:
                # event_bus.publish(...)

    except WebSocketDisconnect:

        logger.info(
            "Device WebSocket disconnected: {}",
            device_id,
        )

    except Exception:

        logger.exception(
            "WebSocket error for device {}",
            device_id,
        )

    finally:

        await device_manager.detach_connection(
            device_id
        )

@router.websocket("/ws/permissions")
async def permission_ws(websocket: WebSocket):
    await websocket.accept()
    # Listen for user approval messages

@router.websocket("/ws/translate")
async def live_translate(ws: WebSocket):
    await ws.accept()
    audio_buffer = b""
    target_lang = "es"
    while True:
        data = await ws.receive_json()
        if data["type"] == "audio":
            audio_buffer += base64.b64decode(data["data"])
            # Process in chunks, run STT -> translation -> TTS -> send back