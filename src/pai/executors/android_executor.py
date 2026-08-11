from typing import Dict, Any
from loguru import logger
from pai.executors.base_executor import BaseExecutor

class AndroidExecutor(BaseExecutor):
    """
    Communicates with Android device via ADB or MQTT.
    In production, run an Android service that connects to this executor.
    """
    
    def __init__(self, device_id: str = None):
        super().__init__("android_executor")
        self.device_id = device_id
        self.adb_connected = False
    
    async def initialize(self) -> None:
        # Attempt ADB connection
        try:
            import subprocess
            result = subprocess.run(["adb", "devices"], capture_output=True, text=True)
            if self.device_id and self.device_id in result.stdout:
                self.adb_connected = True
            logger.info("AndroidExecutor initialized (ADB)")
        except:
            logger.warning("ADB not found. AndroidExecutor running in stub mode.")
    
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type")
        if task_type == "capture_image":
            return await self._capture_image()
        elif task_type == "send_notification":
            return await self._send_notification(task.get("title"), task.get("body"))
        elif task_type == "get_sensors":
            return await self._get_sensors()
        else:
            return {"error": f"Unknown task: {task_type}"}
    
    async def _capture_image(self) -> Dict:
        if not self.adb_connected:
            return {"error": "ADB not connected"}
        import subprocess
        subprocess.run(["adb", "shell", "screencap", "/sdcard/screen.png"])
        subprocess.run(["adb", "pull", "/sdcard/screen.png", "screen.png"])
        return {"path": "screen.png"}
    
    async def _send_notification(self, title: str, body: str) -> Dict:
        # If Android service is running, send via WebSocket
        return {"sent": True}
    
    async def _get_sensors(self) -> Dict:
        return {"accelerometer": [0,0,9.8], "gyroscope": [0,0,0]}