import asyncio
from typing import Dict, Any, List
from loguru import logger
from pai.plugins.base_plugin import BasePlugin

class NotificationPlugin(BasePlugin):
    """Cross‑device notifications with scheduling."""
    name = "notification"
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    async def initialize(self) -> None:
        self._scheduled = []

    async def start(self) -> None:
        self._running = True
        # Implement any startup logic here

    async def shutdown(self) -> None:
        pass
    
    def get_capabilities(self) -> List[str]:
        return ["notification.send", "notification.schedule", "notification.broadcast"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "notification.send":
            return await self._send(params.get('title'), params.get('message'), params.get('target', 'user'))
        elif action == "notification.schedule":
            return await self._schedule(params)
        elif action == "notification.broadcast":
            return await self._broadcast(params.get('title'), params.get('message'))
        raise ValueError(f"Unknown action: {action}")
    
    async def _send(self, title: str, message: str, target: str) -> Dict:
        logger.info(f"[NOTIFICATION] {title}: {message} (target: {target})")
    
        # Also broadcast via WebSocket if target is 'mobile'
        if self.kernel and target == 'mobile':
            await self.kernel.event_bus.publish("notification.mobile", {
                "title": title,
                "message": message
            })
        return {"sent": True}

    async def _schedule(self, params: Dict) -> Dict:
        delay = params.get('delay_seconds', 0)
        asyncio.create_task(self._delayed_notification(params['title'], params['message'], delay))
        return {"scheduled": True, "delay": delay}
    
    async def _delayed_notification(self, title: str, message: str, delay: int):
        await asyncio.sleep(delay)
        await self._send(title, message, "scheduled")
    
    async def _broadcast(self, title: str, message: str) -> Dict:
        # Send to all connected devices
        return {"broadcast": True, "devices": ["mobile", "desktop", "web"]}

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True

    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        logger.info(f"Notification plugin received event: {event} with data: {data}")