import os
from typing import Any
from twilio.rest import Client
from pai.plugins.base_plugin import BasePlugin

class WhatsAppPlugin(BasePlugin):
    name = "whatsapp"
    
    async def initialize(self):
        self.account_sid = os.getenv("TWILIO_SID")
        self.auth_token = os.getenv("TWILIO_AUTH")
        self.client = Client(self.account_sid, self.auth_token)
    
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