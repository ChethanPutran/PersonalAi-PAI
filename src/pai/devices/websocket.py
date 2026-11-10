"""WebSocket-backed device connection."""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger

from pai.devices.connection import (
    ConnectionState,
    DeviceConnection,
)


class WebSocketDeviceConnection(DeviceConnection):
    """
    Server-side representation of a connected device.

    The FastAPI route owns the WebSocket handshake (`websocket.accept()`).
    This class owns connection state and communication with the
    already-accepted WebSocket.

    Lifecycle:

        route:     websocket.accept()
        route:     connection.connect()          ← marks CONNECTED
        route:     device_manager.attach_connection(connection)
        route:     while receive_json(): ...     ← message loop
        route:     finally: device_manager.detach_connection()
                       → connection.disconnect() ← marks DISCONNECTED

    `disconnect()` is idempotent — every cleanup path (route `finally`,
    `DeviceManager.detach_connection`, `DeviceManager.shutdown`, uvicorn
    shutdown) may call it, and only the first call actually closes the
    WebSocket. Subsequent calls return immediately.
    """

    def __init__(
        self,
        device_id: str,
        websocket: WebSocket,
    ) -> None:
        super().__init__(device_id=device_id)

        self.websocket = websocket

        # Track close state independently of `_state`. `_state` reflects
        # the logical connection; `_closed` reflects whether we've
        # actually tried to close the socket. Separating them means a
        # partial close (e.g. peer disconnected first) still sets
        # `_closed`, so a later `disconnect()` won't retry.
        self._closed = False

        # Serialize state transitions. Multiple cleanup paths run
        # concurrently during shutdown; without this lock, two of them
        # can both enter `disconnect()` before either sets `_closed`.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Mark the already-accepted WebSocket as connected.

        Does NOT call `websocket.accept()` — the route already did.
        This method only updates state.
        """
        async with self._lock:
            if self._state == ConnectionState.CONNECTED:
                return
            if self._closed:
                raise RuntimeError(
                    f"Cannot connect device {self.device_id}: "
                    f"connection was already closed"
                )
            self._state = ConnectionState.CONNECTED

        logger.info(
            "WebSocket connection established: {}", self.device_id,
        )

    async def disconnect(self) -> None:
        """
        Close the WebSocket. Idempotent.

        On the first call this sends a close frame and marks the
        connection closed. Every subsequent call — from the route's
        `finally`, `detach_connection`, `shutdown`, or uvicorn's own
        teardown — returns without touching the socket.

        Swallows every exception that means "the socket is already gone":

          - WebSocketDisconnect: peer closed first
          - RuntimeError: ASGI channel already closed by uvicorn
          - Exception: anything else, logged at warning

        Raising here would break the caller (`detach_connection`), which
        has its own shutdown responsibilities. A failed close is not a
        reason to skip those.
        """
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._state = ConnectionState.DISCONNECTED

        # Close outside the lock — awaiting inside would deadlock
        # any concurrent `send()` that's holding the lock.
        try:
            await self.websocket.close(code=1000)
            logger.info("WebSocket disconnected: {}", self.device_id)

        except WebSocketDisconnect:
            # Peer closed before we did. Common during shutdown and
            # during reconnect storms. Not an error.
            logger.debug(
                "WebSocket already disconnected by peer: {}",
                self.device_id,
            )

        except RuntimeError as exc:
            # Starlette raises this when the ASGI send channel is
            # already closed — uvicorn sent its own close frame before
            # us. Same situation as above, different exception.
            logger.debug(
                "WebSocket close skipped for {} (ASGI already closed): {}",
                self.device_id, exc,
            )

        except Exception as exc:
            # Genuinely unexpected. Log but do not re-raise — the caller
            # must complete its cleanup regardless.
            logger.warning(
                "WebSocket close failed for {}: {}",
                self.device_id, exc,
            )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send a JSON payload to the device.

        Raises `RuntimeError` if the connection is not open, or if the
        send itself fails. On failure, marks the connection closed so
        the next `send()` fails fast rather than retrying a dead socket.
        """
        # Fast-path check — no lock needed for a boolean read
        if not self.connected:
            raise RuntimeError(
                f"Device {self.device_id} is not connected"
            )

        try:
            await self.websocket.send_json(payload)
            return {
                "ok": True,
                "device_id": self.device_id,
            }

        except WebSocketDisconnect as exc:
            # Peer closed during send. Mark closed so subsequent sends
            # fail immediately.
            async with self._lock:
                self._closed = True
                self._state = ConnectionState.DISCONNECTED

            logger.info(
                "WebSocket disconnected during send: {}", self.device_id,
            )
            raise RuntimeError(
                f"Device {self.device_id} disconnected during send"
            ) from exc

        except Exception as exc:
            async with self._lock:
                self._closed = True
                self._state = ConnectionState.DISCONNECTED

            logger.exception(
                "Failed to send WebSocket message to {}",
                self.device_id,
            )
            raise RuntimeError(
                f"Failed to send message to device "
                f"{self.device_id}: {exc}"
            ) from exc