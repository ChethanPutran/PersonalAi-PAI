"""
Desktop executor.

Provides controlled access to:
- applications
- files
- browser automation
- mouse interaction
- AutoHotKey on Windows
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from pai.executors.base import BaseExecutor


class DesktopExecutor(BaseExecutor):
    """
    Executor for operations on the local desktop.
    """

    def __init__(
        self,
        kernel: Any = None,
    ) -> None:
        super().__init__(
            name="desktop_executor",
            kernel=kernel,
        )

        self.os_type = platform.system()

    async def initialize(self) -> None:
        self._initialized = True

        logger.info(
            "DesktopExecutor initialized on {}",
            self.os_type,
        )

    async def execute_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if not self.running:
            return {
                "status": "failed",
                "error": "DesktopExecutor is not running",
            }

        task_type = task.get("type")

        handlers = {
            "browser_navigate": self._browser_navigate,
            "open_app": self._open_app,
            "file_operation": self._file_operation,
            "desktop_click": self._desktop_click,
            "ahk": self._ahk_command,
        }

        handler = handlers.get(task_type)

        if handler is None:
            return {
                "status": "failed",
                "error": f"Unsupported desktop task: {task_type}",
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
                "Desktop task failed: {}",
                task_type,
            )

            return {
                "status": "failed",
                "executor": self.name,
                "task_type": task_type,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    async def _browser_navigate(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        url = task.get("url")

        if not url:
            raise ValueError("browser_navigate requires 'url'")

        if self.kernel is None:
            return {
                "error": "Kernel is not available for browser plugin"
            }

        plugin_manager = getattr(
            self.kernel,
            "plugin_manager",
            None,
        )

        if plugin_manager is None:
            return {
                "error": "Plugin manager is not available"
            }

        return await plugin_manager.execute_plugin(
            "browser",
            "browser.navigate",
            {"url": url},
        )

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------

    async def _open_app(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        app_name = task.get("app_name")

        if not app_name:
            raise ValueError("open_app requires 'app_name'")

        if self.os_type == "Windows":
            process = await asyncio.create_subprocess_shell(
                f'start "" "{app_name}"'
            )

        elif self.os_type == "Darwin":
            process = await asyncio.create_subprocess_exec(
                "open",
                "-a",
                app_name,
            )

        else:
            process = await asyncio.create_subprocess_exec(
                app_name,
            )

        await process.wait()

        return {
            "opened": True,
            "app": app_name,
        }

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def _file_operation(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        operation = task.get("operation")
        path = task.get("path")

        if not operation:
            raise ValueError("file_operation requires 'operation'")

        if not path:
            raise ValueError("file_operation requires 'path'")

        target = Path(path)

        if operation == "read":

            if not target.exists():
                raise FileNotFoundError(path)

            content = await asyncio.to_thread(
                target.read_text
            )

            return {
                "path": str(target),
                "content": content,
            }

        if operation == "write":

            content = task.get("content", "")

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            await asyncio.to_thread(
                target.write_text,
                content,
            )

            return {
                "path": str(target),
                "written": True,
            }

        if operation == "delete":

            if target.exists():
                await asyncio.to_thread(
                    target.unlink
                )

            return {
                "path": str(target),
                "deleted": True,
            }

        raise ValueError(
            f"Unsupported file operation: {operation}"
        )

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    async def _desktop_click(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        x = task.get("x")
        y = task.get("y")

        if x is None or y is None:
            raise ValueError(
                "desktop_click requires x and y"
            )

        try:
            import pyautogui
        except ImportError:
            raise RuntimeError(
                "pyautogui is not installed"
            )

        await asyncio.to_thread(
            pyautogui.click,
            x,
            y,
        )

        return {
            "clicked": True,
            "x": x,
            "y": y,
        }

    # ------------------------------------------------------------------
    # AutoHotKey
    # ------------------------------------------------------------------

    async def _ahk_command(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if self.os_type != "Windows":
            raise RuntimeError(
                "AutoHotKey is supported only on Windows"
            )

        script = task.get("script", "")

        if not script:
            raise ValueError("ahk requires 'script'")

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".ahk",
            delete=False,
        ) as file:

            file.write(script)
            script_path = file.name

        try:
            process = await asyncio.create_subprocess_exec(
                "autohotkey.exe",
                script_path,
            )

            return_code = await process.wait()

            if return_code != 0:
                raise RuntimeError(
                    f"AutoHotKey exited with code {return_code}"
                )

            return {
                "executed": True,
            }

        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass