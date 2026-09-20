"""
Android executor using ADB.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any, Dict, Optional

from loguru import logger

from pai.executors.base import BaseExecutor


class AndroidExecutor(BaseExecutor):
    """
    Android device executor.

    Uses ADB when available.

    A future Android-side service can be plugged into the same
    executor interface without changing the scheduler.
    """

    def __init__(
        self,
        device_id: Optional[str] = None,
    ) -> None:

        super().__init__(
            name="android_executor",
        )

        self.device_id = device_id
        self.adb_path: Optional[str] = None
        self.adb_connected = False

    async def initialize(self) -> None:

        self.adb_path = shutil.which("adb")

        if self.adb_path is None:
            logger.warning(
                "ADB not found. AndroidExecutor unavailable."
            )

            self._initialized = True
            return

        self.adb_connected = await self._check_connection()

        self._initialized = True

        logger.info(
            "AndroidExecutor initialized. connected={}",
            self.adb_connected,
        )

    async def _check_connection(self) -> bool:

        if not self.adb_path:
            return False

        process = await asyncio.create_subprocess_exec(
            self.adb_path,
            "devices",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, _ = await process.communicate()

        if process.returncode != 0:
            return False

        output = stdout.decode(
            errors="ignore"
        )

        devices = []

        for line in output.splitlines():
            if "\tdevice" in line:
                devices.append(
                    line.split("\t")[0]
                )

        if self.device_id:
            return self.device_id in devices

        return bool(devices)

    async def start(self) -> None:

        if not self._initialized:
            await self.initialize()

        self.adb_connected = await self._check_connection()

        self._running = True

        logger.info(
            "AndroidExecutor started. connected={}",
            self.adb_connected,
        )

    async def execute_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if not self.running:
            return {
                "status": "failed",
                "error": "AndroidExecutor is not running",
            }

        task_type = task.get("type")

        handlers = {
            "capture_image": self._capture_image,
            "send_notification": self._send_notification,
            "get_sensors": self._get_sensors,
        }

        handler = handlers.get(task_type)

        if handler is None:
            return {
                "status": "failed",
                "error": f"Unknown Android task: {task_type}",
            }

        try:
            result = await handler(task)

            return {
                "status": "success",
                "executor": self.name,
                "task_type": task_type,
                "result": result,
            }

        except Exception as exc:
            logger.exception(
                "Android task failed"
            )

            return {
                "status": "failed",
                "executor": self.name,
                "task_type": task_type,
                "error": str(exc),
            }

    async def _run_adb(
        self,
        *args: str,
    ) -> str:

        if not self.adb_path:
            raise RuntimeError("ADB is not installed")

        command = [
            self.adb_path,
        ]

        if self.device_id:
            command.extend(
                ["-s", self.device_id]
            )

        command.extend(args)

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(
                stderr.decode(
                    errors="ignore"
                ).strip()
                or "ADB command failed"
            )

        return stdout.decode(
            errors="ignore"
        ).strip()

    async def _capture_image(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        remote_path = task.get(
            "remote_path",
            "/sdcard/screen.png",
        )

        local_path = task.get(
            "local_path",
            "./screen.png",
        )

        await self._run_adb(
            "shell",
            "screencap",
            "-p",
            remote_path,
        )

        await self._run_adb(
            "pull",
            remote_path,
            local_path,
        )

        return {
            "path": local_path,
        }

    async def _send_notification(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        title = task.get("title", "")
        body = task.get("body", "")

        # This requires an Android-side application/service.
        logger.warning(
            "Android notification requires an Android service"
        )

        return {
            "sent": False,
            "title": title,
            "body": body,
            "reason": "Android service not configured",
        }

    async def _get_sensors(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        # Placeholder until the Android service exposes sensors.
        return {
            "accelerometer": [0.0, 0.0, 9.8],
            "gyroscope": [0.0, 0.0, 0.0],
            "source": "placeholder",
        }

    async def stop(self) -> None:
        self._running = False
        logger.info("AndroidExecutor stopped")

    async def shutdown(self) -> None:
        self.adb_connected = False
        await super().shutdown()