from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket
from loguru import logger

from pai.devices.connection import (
    ConnectionState,
    DeviceConnection,
)


class WebSocketDeviceConnection(DeviceConnection):
    """
    Server-side representation of a connected device.

    The FastAPI route owns the WebSocket handshake.
    This class owns the connection state and communication
    with the already-accepted WebSocket.
    """

    def __init__(
        self,
        device_id: str,
        websocket: WebSocket,
    ) -> None:
        super().__init__(
            device_id=device_id,
        )

        self.websocket = websocket

    async def connect(self) -> None:
        """
        Mark the already-accepted WebSocket as connected.

        The WebSocket handshake is intentionally NOT performed
        here. The route calls websocket.accept().
        """

        self._state = ConnectionState.CONNECTED

        logger.info(
            "WebSocket connection established: {}",
            self.device_id,
        )

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""

        if self._state == ConnectionState.DISCONNECTED:
            return

        try:
            await self.websocket.close()
        except Exception as exc:
            logger.debug(
                "WebSocket close failed for {}: {}",
                self.device_id,
                exc,
            )

        self._state = ConnectionState.DISCONNECTED

        logger.info(
            "WebSocket disconnected: {}",
            self.device_id,
        )

    async def send(
        self,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Send a JSON payload to the connected device.
        """

        if not self.connected:
            raise RuntimeError(
                f"Device {self.device_id} is not connected"
            )

        try:
            await self.websocket.send_json(
                payload
            )

            return {
                "ok": True,
                "device_id": self.device_id,
            }

        except Exception as exc:
            logger.exception(
                "Failed to send WebSocket message to {}",
                self.device_id,
            )

            self._state = ConnectionState.DISCONNECTED

            raise RuntimeError(
                f"Failed to send message to device "
                f"{self.device_id}: {exc}"
            ) from exc