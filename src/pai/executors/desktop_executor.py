import asyncio
import os
import subprocess
import platform
from typing import Dict, Any
from loguru import logger
from pai.executors.base_executor import BaseExecutor

class DesktopExecutor(BaseExecutor):
    """Executes browser automation, file ops, app control on desktop."""
    
    def __init__(self):
        super().__init__("desktop_executor")
        self.os_type = platform.system()
    
    async def initialize(self) -> None:
        # Launch browser automation service if needed
        logger.info(f"DesktopExecutor initialized on {self.os_type}")
    
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type")
        if task_type == "browser_navigate":
            return await self._browser_navigate(task.get("url"))
        elif task_type == "open_app":
            return await self._open_app(task.get("app_name"))
        elif task_type == "file_operation":
            return await self._file_op(task.get("operation"), task.get("path"), task.get("content"))
        elif task_type == "desktop_click":
            return await self._desktop_click(task.get("x"), task.get("y"))
        else:
            return {"error": f"Unsupported task type: {task_type}"}
    
    async def _browser_navigate(self, url: str) -> Dict:
        # Use Playwright or Selenium via plugin
        if self.kernel:
            return await self.kernel.plugin_manager.execute_plugin("browser", "navigate", {"url": url})
        return {"error": "Browser plugin not available"}
    
    async def _open_app(self, app_name: str) -> Dict:
        try:
            if self.os_type == "Windows":
                subprocess.Popen(f"start {app_name}", shell=True)
            elif self.os_type == "Darwin":  # macOS
                subprocess.Popen(["open", "-a", app_name])
            else:  # Linux
                subprocess.Popen([app_name])
            return {"status": "opened", "app": app_name}
        except Exception as e:
            return {"error": str(e)}
    
    async def _file_op(self, operation: str, path: str, content: str = None) -> Dict:
        import os
        try:
            if operation == "read":
                with open(path, "r") as f:
                    return {"content": f.read()}
            elif operation == "write":
                with open(path, "w") as f:
                    f.write(content)
                return {"status": "written"}
            elif operation == "delete":
                os.remove(path)
                return {"status": "deleted"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _desktop_click(self, x: int, y: int) -> Dict:
        # Use PyAutoGUI
        try:
            import pyautogui
            pyautogui.click(x, y)
            return {"clicked": (x, y)}
        except ImportError:
            return {"error": "PyAutoGUI not installed"}
        

    async def _ahk_command(self, script: str) -> Dict:
        """Execute an AutoHotKey script."""
        import tempfile
        import subprocess
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ahk', delete=False) as f:
            f.write(script)
            path = f.name
        subprocess.run(["autohotkey.exe", path])
        os.unlink(path)
        return {"executed": True}

    async def execute_task(self, task: Dict) -> Dict:
        if task["type"] == "ahk":
            return await self._ahk_command(task["script"])
        # ... other types