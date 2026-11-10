"""Executor that routes device-plugin invocations over WebSocket."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from loguru import logger

from pai.devices.manager import DeviceManager
from pai.executors.base import BaseExecutor
from pai.observability.tracer import get_tracer
from pai.protocol.enums import DeviceMessageType, DeviceControlOp

class DeviceExecutor(BaseExecutor):
    """
    Executes capabilities that live on a connected device.

    Routed by CapabilityRouter when the capability is provided by a
    plugin whose manifest declares `runtime.kind == "native"` (camera,
    notifications, etc.). Sends an `invoke` payload over the device
    WebSocket and awaits the correlated `result` message.

    Requires:
      - DeviceManager (to send via WebSocket and receive results)
      - A response registry keyed by request_id to correlate
    """

    def __init__(self, device_manager: DeviceManager) -> None:
        super().__init__(
            name="device_executor",
            executor_id=str(uuid.uuid4()),
        )
        self.device_manager = device_manager
        # request_id → asyncio.Future[Dict]
        self._pending: Dict[str, asyncio.Future] = {}

        # task_id → (request_id, device_id)
        self._in_flight: Dict[str, tuple[str, str]] = {}

    async def initialize(self) -> None:
        self._initialized = True
        logger.info("DeviceExecutor initialized")

    async def start(self) -> None:
        if not self._initialized:
            await self.initialize()
        self._running = True
        logger.info("DeviceExecutor started")


    # ------------------------------------------------------------------
    # Pause / resume / cancel — forwarded to the device if it supports it
    # ------------------------------------------------------------------

    async def pause_task(self, task_id: str) -> bool:
        return await self._send_control(task_id, DeviceControlOp.PAUSE)

    async def resume_task(self, task_id: str) -> bool:
        return await self._send_control(task_id, DeviceControlOp.RESUME)

    async def cancel_task(self, task_id: str) -> bool:
        entry = self._in_flight.pop(task_id, None)
        if entry is None:
            return False
        request_id, device_id = entry

        # Tell the device (best-effort — the ack proves receipt but the
        # plugin may not be cancellable mid-call).
        try:
            await self.device_manager.send(device_id, {
                "type": DeviceMessageType.CONTROL.value,
                "op":   DeviceControlOp.CANCEL.value,
                "request_id": request_id,
            })
        except Exception as exc:
            logger.warning(
                "DeviceExecutor: cancel frame failed for task={}: {}",
                task_id, exc,
            )

        # Cancel the local future immediately. This is the authoritative
        # signal — even if the device ignores the frame, the backend
        # stops waiting.
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.cancel()
        return True

    async def _send_control(
        self,
        task_id: str,
        op: DeviceControlOp,
    ) -> bool:
        entry = self._in_flight.get(task_id)
        if entry is None:
            return False
        request_id, device_id = entry
        try:
            await self.device_manager.send(device_id, {
                "type": DeviceMessageType.CONTROL.value,
                "op":   op.value,
                "request_id": request_id,
            })
            logger.info(
                "DeviceExecutor: {} frame sent for task={} (req={})",
                op.value, task_id, request_id,
            )
            return True
        except Exception as exc:
            logger.warning(
                "DeviceExecutor: {} failed for task={}: {}",
                op.value, task_id, exc,
            )
            return False

    async def invoke(
        self,
        device_id: str,
        capability: str,
        parameters: Dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """
        Send a capability call to `device_id` over the WS and wait for
        the matching result message. Raises asyncio.TimeoutError if the
        device doesn't reply in `timeout` seconds.
        """
        request_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self.device_manager.send(device_id, {
                "type": "task",
                "request_id": request_id,
                "capability": capability,
                "parameters": parameters,
            })
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(request_id, None)

            
    # Called by the WebSocket route when a device sends a `result` frame
    def deliver_result(self, request_id: str, payload: Dict[str, Any]) -> bool:
        """
        Resolve the pending future for `request_id`, if any.

        Returns True if this executor owned the request and delivered
        the payload, False otherwise (already timed out, wrong
        executor, etc.).
        """
        fut = self._pending.pop(request_id, None)
        if fut is None:
            logger.debug(
                "DeviceExecutor: no pending future for request_id={}",
                request_id,
            )
            return False
        if not fut.done():
            fut.set_result(payload)
            return True
        # Future was already cancelled or resolved by someone else —
        # the request timed out or shutdown raced with the frame.
        logger.debug(
            "DeviceExecutor: future for {} already done ({})",
            request_id,
            "cancelled" if fut.cancelled() else "resolved",
        )
        return False

    async def execute_task(self, task: Any) -> Dict[str, Any]:
        task_dict = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        capability = task_dict.get("capability") or task_dict.get("type")
        device_id = task_dict.get("device_id")
        params = task_dict.get("parameters") or {}
        task_id = task_dict.get("id")

        tracer = get_tracer()
        async with tracer.span(
            "executor.device.execute_task",
            task_id=task_id,
            capability=capability,
            device_id=device_id,
        ) as span:

            if not self._running:
                span.set_error("executor_not_running")
                return {
                    "status": "failed",
                    "error": "DeviceExecutor is not running",
                }
            if not device_id:
                span.set_error("missing_device_id")
                return {
                    "status": "failed",
                    "error": "Task has no device_id",
                }

            request_id = str(uuid.uuid4())
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[request_id] = fut
            if task_id:
                self._in_flight[task_id] = (request_id, device_id)


            payload = {
                "type": "invoke",
                "request_id": request_id,
                "capability": capability,
                "parameters": params,
            }

            try:
                await self.device_manager.send(device_id, payload)
                result = await asyncio.wait_for(fut, timeout=30.0)
                span.set_attribute("status", "completed")
                return {
                    "status": "success",
                    "capability": capability,
                    "device_id": device_id,
                    "result": result,
                }
            except asyncio.TimeoutError:
                span.set_error("timeout")
                return {
                    "status": "failed",
                    "error": f"Device {device_id} did not respond in 30s",
                }
            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                return {
                    "status": "failed",
                    "error": str(exc),
                }
            finally:
                self._pending.pop(request_id, None)
                if task_id: 
                    self._in_flight.pop(task_id, None)

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        self._running = False
        # Fail any pending requests
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()