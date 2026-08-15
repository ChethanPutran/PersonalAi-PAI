import os
from typing import Any
from twilio.rest import Client
from pai.plugins.base_plugin import BasePlugin
from loguru import logger

class WhatsAppPlugin(BasePlugin):
    name = "whatsapp"
    
    async def initialize(self):
        self.account_sid = os.getenv("TWILIO_SID")
        self.auth_token = os.getenv("TWILIO_AUTH")
        self.client = Client(self.account_sid, self.auth_token)


    async def start(self) -> None:
        self._running = True
        # Implement any startup logic here

    async def shutdown(self) -> None:
        pass

    def get_capabilities(self):
        return ["whatsapp.send_message", "whatsapp.read_messages"]
    
    async def execute(self, action, params)->Any:
        if action == "whatsapp.send_message":
            message = self.client.messages.create(
                from_='whatsapp:+14155238886',
                body=params["text"],
                to=f'whatsapp:{params["to"]}'
            )
            return {"sid": message.sid}

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True

    async def handle_event(self, event: str, data: dict) -> None:
        # Implement your event handling logic here
        logger.info(f"WhatsApp plugin received event: {event} with data: {data}")