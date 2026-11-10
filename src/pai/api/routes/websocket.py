"""Device WebSocket routes."""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from pai.app_context import get_ws_app_context
from pai.devices.websocket import WebSocketDeviceConnection
from pai.protocol.enums import TASK_EVENT_TYPES, DeviceMessageType


router = APIRouter()


# ============================================================
# Device WebSocket
# ============================================================

@router.websocket("/{device_id}/ws")
async def device_websocket(websocket: WebSocket, device_id: str):
    """
    Persistent WebSocket for a specific device.

    Sequence:
      1. Validate JWT from ?token= query param.
      2. Look up the device; reject if unknown or owned by another user.
      3. Accept the socket (required before send_json / receive_json).
      4. Attach to DeviceManager (sets ONLINE status).
      5. Loop: ping / heartbeat / result / event.
      6. On disconnect: detach (closes and sets OFFLINE).

    The connection wrapper is idempotent, so this handler and
    DeviceManager.detach_connection can both call disconnect() without
    triggering the ASGI "Unexpected websocket.close" error.
    """

    # ---- 1. Authenticate ----
    token = websocket.query_params.get("token")
    user_id: Optional[str] = None

    if token:
        try:
            from pai.security.auth import decode_access_token
            user_id = decode_access_token(token)
        except Exception as exc:
            logger.warning(
                "WS token decode failed for device {}: {}",
                device_id, exc,
            )
            user_id = None

    if user_id is None:
        # Per ASGI spec, close() without accept() is a valid rejection.
        await websocket.close(code=4401)
        logger.warning(
            "Rejected WS for device {}: missing/invalid token",
            device_id,
        )
        return

    # ---- 2. Look up device (must exist before we accept) ----
    context = get_ws_app_context(websocket)
    device_manager = context.orchestrator.device_manager

    device = await device_manager.get(device_id)
    if device is None:
        await websocket.close(code=1008)
        logger.warning(
            "Rejected WS for device {}: not registered", device_id,
        )
        return

    # Ownership check — if the device is bound to a user, the
    # token must match. An unowned device may connect (bootstrap).
    owner = getattr(device, "user_id", None)
    if owner and owner != user_id:
        await websocket.close(code=4003)
        logger.warning(
            "Rejected WS for device {}: owned by {}, caller is {}",
            device_id, owner, user_id,
        )
        return

    # ---- 3. Accept + attach ----
    await websocket.accept()
    logger.info(
        "Device WS connected: {} (user={})", device_id, user_id,
    )

    connection = WebSocketDeviceConnection(
        device_id=device_id,
        websocket=websocket,
    )

    event_bus = context.orchestrator.event_bus
    if event_bus is not None:
        await event_bus.publish(
            "device.connected",
            {
                "device_id": device_id,
                "user_id": user_id,
            },
        )
    else:
        raise RuntimeError("EventBus not available in context")

    try:
        await connection.connect()
        await device_manager.attach_connection(device_id, connection)

        # ---- 4. Message loop ----
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info("Device WS disconnected: {}", device_id)
                break
            except Exception as exc:
                # Malformed JSON — keep the connection alive so a
                # single bad frame doesn't kill the whole session.
                logger.warning(
                    "Malformed WS message from {}: {}", device_id, exc,
                )
                continue

            message_type = message.get("type")

            try:

                # ── Fast path: task / plan events fan out to connected clients ──
                if message_type in TASK_EVENT_TYPES:
                    await event_bus.publish(
                        message_type,
                        {"device_id": device_id, **message},
                    )
                    continue

                message_type = message.get("type")

                match message_type:
                    case DeviceMessageType.PING.value:
                        await websocket.send_json({
                            "type": DeviceMessageType.PONG.value,
                            "device_id": device_id,
                        })

                    case DeviceMessageType.PONG.value:
                        # Device acknowledged one of our pings — nothing to do.
                        logger.debug("Pong from device {}", device_id)

                    case DeviceMessageType.HEARTBEAT.value:
                        await device_manager.registry.heartbeat(device_id)
                        await websocket.send_json({
                            "type": DeviceMessageType.HEARTBEAT_ACK.value,
                            "device_id": device_id,
                        })

                    case DeviceMessageType.RESULT.value:
                        # Tool result from the device. Route it to the
                        # executor waiting on this call; publish to the
                        # event bus as a fallback signal.
                        request_id = message.get("request_id")
                        logger.info(
                            "Result from {}: request_id={}",
                            device_id, request_id,
                        )

                        executor_manager = context.orchestrator.executor_manager
                        if executor_manager is None:
                            logger.error(
                                "No executor manager to deliver result {} "
                                "from device {}", request_id, device_id,
                            )
                            raise RuntimeError(
                                "ExecutorManager not available in context",
                            )

                        if request_id:
                            try:
                                executor_manager.deliver_result(
                                    request_id, message,
                                )
                            except Exception as exc:
                                logger.error(
                                    "Failed to deliver result {} "
                                    "to executor: {}",
                                    request_id, exc,
                                )
                                raise

                        # Publish a bus event so any listener sees it too.
                        try:
                            await event_bus.publish(
                                "device.result",
                                {
                                    "device_id": device_id,
                                    "request_id": request_id,
                                    "result": message,
                                },
                            )
                        except Exception as exc:
                            logger.debug(
                                "Failed to publish device.result: {}", exc,
                            )

                    case DeviceMessageType.CONTROL_ACK.value:
                        # Device acknowledged a pause/resume/cancel.
                        # Logged so operators can see the round-trip;
                        # no state change needed here because
                        # `TaskManager` already flipped the status
                        # optimistically.
                        logger.debug(
                            "Control ack from {}: op={} req={} ok={} err={}",
                            device_id,
                            message.get("op"),
                            message.get("request_id"),
                            message.get("ok"),
                            message.get("error"),
                        )

                    case DeviceMessageType.EVENT.value:
                        event_name = message.get("event", "unknown")
                        logger.info(
                            "Event from {}: {}", device_id, event_name,
                        )
                        try:
                            await event_bus.publish(
                                f"device.{event_name}",
                                {
                                    "device_id": device_id,
                                    "data": message.get("data", {}),
                                },
                            )
                        except Exception as exc:
                            logger.debug(
                                "Failed to publish device event: {}", exc,
                            )

                    case DeviceMessageType.SYNC_PLUGINS_RESPONSE.value:
                        # Device's response to our `sync_plugins_request`.
                        # The plugin manager can reconcile from this — for
                        # now just log so operators can see the payload.
                        logger.debug(
                            "sync_plugins_response from {}: {} plugin(s)",
                            device_id,
                            len(message.get("plugins", []) or []),
                        )

                    case _:
                        logger.debug(
                            "Unknown WS message type from {}: {}",
                            device_id, message_type,
                        )
            except WebSocketDisconnect:
                logger.info(
                    "Device WS disconnected mid-send: {}", device_id,
                )
                break
            except asyncio.CancelledError:
                pass 
            except Exception as exc:
                # A single failed message shouldn't kill the loop.
                logger.warning(
                    "Error processing WS message from {}: {}",
                    device_id, exc,
                )
            finally:
                try:
                    await device_manager.detach_connection(device_id)
                except Exception as exc:
                    logger.debug("detach during shutdown: {}", exc)
                # Do NOT call websocket.close() here — uvicorn handles it, and
                # calling it after ASGI teardown is what triggers the warning.

    except WebSocketDisconnect:
        logger.info("Device WS disconnected: {}", device_id)

    except Exception:
        logger.exception(
            "Unexpected WS error for device {}", device_id,
        )

    finally:
        # detach_connection() calls connection.disconnect() internally,
        # which is idempotent — the peer already closed, so the
        # disconnect() will be a no-op and won't produce the ASGI
        # double-close error.
        try:
            await device_manager.detach_connection(device_id)
        except Exception as exc:
            logger.warning(
                "Failed to detach device {}: {}", device_id, exc,
            )
        logger.info("Device WS cleanup complete: {}", device_id)


# ============================================================
# Permission WebSocket — approval flow
# ============================================================

@router.websocket("/ws/permissions")
async def permission_ws(websocket: WebSocket):
    """Listen for file-access approval messages."""
    await websocket.accept()
    logger.info("Permission WS connected")
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            logger.info("Permission message: {}", message)
            # TODO: route to FilePermissionRequest handler — e.g.
            #   await permission_service.handle_decision(message)
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("Permission WS disconnected")


# ============================================================
# Translation WebSocket — streaming audio
# ============================================================

@router.websocket("/ws/translate")
async def live_translate(websocket: WebSocket):
    """Receive audio chunks, return translations."""
    await websocket.accept()
    logger.info("Translation WS connected")

    audio_buffer = bytearray()
    target_lang = "es"
    MAX_BUFFER = 10 * 1024 * 1024  # 10 MB cap

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.warning("Translation bad message: {}", exc)
                continue

            msg_type = data.get("type")

            if msg_type == "audio":
                try:
                    chunk = base64.b64decode(data.get("data", ""))
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Invalid base64: {exc}",
                    })
                    continue

                audio_buffer.extend(chunk)

                if len(audio_buffer) > MAX_BUFFER:
                    logger.warning(
                        "Translation buffer exceeded {} bytes; dropping",
                        MAX_BUFFER,
                    )
                    audio_buffer.clear()
                    await websocket.send_json({
                        "type": "error",
                        "message": "Buffer overflow, cleared",
                    })
                    continue

                # TODO: run STT -> translation -> TTS on audio_buffer,
                # then send back:
                #   await websocket.send_json({
                #       "type": "translation",
                #       "lang": target_lang,
                #       "text": ...,
                #       "audio_b64": ...,
                #   })

            elif msg_type == "set_target_lang":
                target_lang = data.get("lang", target_lang)
                await websocket.send_json({
                    "type": "target_lang_set",
                    "lang": target_lang,
                })

            elif msg_type == "reset":
                audio_buffer.clear()
                await websocket.send_json({"type": "reset_ok"})

            else:
                logger.debug(
                    "Unknown translation message type: {}", msg_type,
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Unexpected translation WS error")
    finally:
        logger.info("Translation WS disconnected")