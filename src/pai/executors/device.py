"""Executor that routes device-plugin invocations over WebSocket."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

from loguru import logger

from pai.devices.manager import DeviceManager
from pai.devices.models import DeviceFrame, DeviceMessage
from pai.executors.base import BaseExecutor
from pai.observability.tracer import get_tracer
from pai.common.enums.device import DeviceMessageType, DeviceControlOp, DeviceOpStatus
from pai.tasks.models import Task, TaskResult, get_task_timeout, get_task_timeout



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

    supports_control_ack = True

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

        # (request_id, operation) → Future[DeviceFrame] for control acks
        self._pending_acks: Dict[tuple[str, str], asyncio.Future] = {}
        self.control_ack_timeout: float = 5.0
        self.cancel_ack_timeout: float = 3.0

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
        return await self._send_control(task_id, DeviceControlOp.PAUSE, DeviceOpStatus.RUNNING)

    async def resume_task(self, task_id: str) -> bool:
        return await self._send_control(task_id, DeviceControlOp.RESUME, DeviceOpStatus.RUNNING)

    async def cancel_task(self, task_id: str) -> bool:
        """Stop waiting NOW and ask the device to stop.

        Returns True iff the device ACKNOWLEDGED the cancel (``control_ack``) in time. The
        local wait is cancelled either way — the ack only tells us whether the device
        confirms it stopped (False = "abandoned": it may still have completed the action).
        """
        entry = self._in_flight.pop(task_id, None)
        if entry is None:
            return False
        request_id, device_id = entry

        # Authoritative and immediate: even if the device ignores the frame, the backend
        # stops waiting.
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.cancel()

        key = (request_id, DeviceControlOp.CANCEL.value)
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_acks[key] = ack
        try:
            await self.device_manager.send(device_id, DeviceMessage(
                type=DeviceMessageType.CONTROL,
                operation=DeviceControlOp.CANCEL,
                request_id=request_id,
                task_id=task_id,
                status=DeviceOpStatus.RUNNING,
            ))
            frame: DeviceFrame = await asyncio.wait_for(ack, self.cancel_ack_timeout)
            return frame.status not in (DeviceOpStatus.FAILURE.value, DeviceOpStatus.NONE.value)
        except asyncio.TimeoutError:
            logger.warning("DeviceExecutor: no cancel ack for task={} (abandoned)", task_id)
            return False
        except Exception as exc:
            logger.warning("DeviceExecutor: cancel frame failed for task={}: {}", task_id, exc)
            return False
        finally:
            self._pending_acks.pop(key, None)

    async def _send_control(
        self,
        task_id: str,
        op: DeviceControlOp,
        status: DeviceOpStatus,
    ) -> bool:
        """Send a CONTROL frame and wait (bounded) for the device's CONTROL_ACK.

        True  -> device acknowledged and did not report failure.
        False -> not in flight, send failed, no ack in time, or device refused.
        """
        entry = self._in_flight.get(task_id)
        if entry is None:
            return False
        request_id, device_id = entry
        key = (request_id, op.value)
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_acks[key] = ack
        try:
            await self.device_manager.send(device_id, DeviceMessage(
                type=DeviceMessageType.CONTROL,
                operation=op,
                request_id=request_id,
                task_id=task_id,
                status=status,
            ))
            logger.info(
                "DeviceExecutor: {} frame sent for task={} (req={})",
                op.value, task_id, request_id,
            )
            frame: DeviceFrame = await asyncio.wait_for(ack, self.control_ack_timeout)
            return frame.status not in (DeviceOpStatus.FAILURE.value, DeviceOpStatus.NONE.value)
        except asyncio.TimeoutError:
            logger.warning("DeviceExecutor: no {} ack for task={}", op.value, task_id)
            return False
        except Exception as exc:
            logger.warning(
                "DeviceExecutor: {} failed for task={}: {}",
                op.value, task_id, exc,
            )
            return False
        finally:
            self._pending_acks.pop(key, None)

    def handle_control_ack(self, frame: DeviceFrame) -> bool:
        """Called by the device WS route for a `control_ack` frame."""
        fut = self._pending_acks.get((frame.request_id or "", frame.operation or ""))
        if fut is None or fut.done():
            return False
        fut.set_result(frame)
        return True

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
            await self.device_manager.send(device_id,
                                           DeviceMessage(
                                               type=DeviceMessageType.INVOKE,
                                               request_id=request_id,
                                               capability=capability,
                                               parameters=parameters,
                                           ))

            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(request_id, None)

            
    # Called by the WebSocket route when a device sends a `result` frame
    def deliver_result(self, request_id: str, frame: DeviceFrame) -> bool:
        """
        Resolve the pending future for `request_id`, if any.

        Returns True if this executor owned the request and delivered
        the frame, False otherwise (already timed out, wrong executor).
        """
        fut = self._pending.pop(request_id, None)
        if fut is None:
            logger.debug("DeviceExecutor: no pending future for request_id={}", request_id)
            return False
        if not fut.done():
            fut.set_result(frame)
            return True
        logger.debug(
            "DeviceExecutor: future for {} already done ({})",
            request_id, "cancelled" if fut.cancelled() else "resolved",
        )
        return False

    @staticmethod
    def _source_device_of(task: Task) -> Optional[str]:
        """The device the request came from, as recorded on the task (trusted server data)."""
        auth = getattr(task, "authorization", None)
        src = getattr(auth, "source_device_id", None)
        if not src:
            src = (getattr(task, "metadata", None) or {}).get("source_device_id")
        return src or None

    async def execute_task(self, task: Task) -> TaskResult:
        capability = task.capability
        device_id = task.device_id
        params = task.parameters
        task_id = task.id

        tracer = get_tracer()
        async with tracer.span(
            "executor.device.execute_task",
            task_id=task_id,
            capability=capability,
            device_id=device_id,
        ) as span:

            if not self._running:
                span.set_error("executor_not_running")
                return TaskResult(
                    task_id=task_id,
                    error="Executor is not running",
                )
            if not device_id:
                span.set_error("missing_device_id")
                return TaskResult(
                    task_id=task_id,
                    error="Task has no device_id",
                )

            request_id = str(uuid.uuid4())
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[request_id] = fut
            if task_id:
                self._in_flight[task_id] = (request_id, device_id)

            payload = DeviceMessage(
                type=DeviceMessageType.INVOKE,
                request_id=request_id,
                task_id=task_id,
                capability=capability,
                parameters=params,
                idempotency_key=task.idempotency_key or task.id,
                attempt=task.retry_count + task.recovery_count,
                source_device_id=self._source_device_of(task),
            )

            timeout = get_task_timeout(task)
            if timeout is not None and timeout == float("inf"):
                timeout = None      # long-running / streaming: no deadline

            span.set_attribute("type", task.type)
            span.set_attribute("timeout", timeout)

            task_result = TaskResult(task_id=task_id, success=False)
            try:
                await self.device_manager.send(device_id, payload)

                if timeout is not None:
                    frame: DeviceFrame = await asyncio.wait_for(fut, timeout=timeout)
                else:
                    frame = await fut

                # A reply can itself report failure; don't count it as success.
                if frame.failed:
                    span.set_error("device_reported_failure")
                    task_result.success = False
                    task_result.error = frame.error or "Device reported failure"
                    task_result.output = frame.result
                else:
                    span.set_attribute("status", "completed")
                    task_result.success = True
                    task_result.output = frame.result

            except asyncio.TimeoutError:
                span.set_error("timeout")
                task_result.success = False
                task_result.error = f"Device {device_id} did not respond in {timeout}s"

            except Exception as exc:
                span.set_error(f"{type(exc).__name__}: {exc}")
                task_result.success = False
                task_result.error = str(exc)
            finally:
                self._pending.pop(request_id, None)
                if task_id: 
                    self._in_flight.pop(task_id, None)

            return task_result

    async def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        self._running = False
        # Fail any pending requests
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()