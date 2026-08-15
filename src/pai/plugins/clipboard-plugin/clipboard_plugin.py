import pyperclip
from pai.plugins.base_plugin import BasePlugin
from typing import Any
from loguru import logger

class ClipboardPlugin(BasePlugin):
    name = "clipboard"

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
            self._running = True
            # Implement any startup logic here

    async def shutdown(self) -> None:
        pass
    
    def get_capabilities(self):
        return ["clipboard.read", "clipboard.write"]
    
    async def execute(self, action, params)-> Any:
        if action == "clipboard.read":
            return {"text": pyperclip.paste()}
        elif action == "clipboard.write":
            pyperclip.copy(params["text"])
            return {"written": True}

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True

    async def handle_event(self, event: str, data: dict) -> None:
        # Implement your event handling logic here
        logger.info(f"Clipboard plugin received event: {event} with data: {data}")

    