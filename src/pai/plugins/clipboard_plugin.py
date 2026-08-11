import pyperclip
from pai.plugins.base_plugin import BasePlugin
from typing import Any

class ClipboardPlugin(BasePlugin):
    name = "clipboard"
    
    def get_capabilities(self):
        return ["clipboard.read", "clipboard.write"]
    
    async def execute(self, action, params)-> Any:
        if action == "clipboard.read":
            return {"text": pyperclip.paste()}
        elif action == "clipboard.write":
            pyperclip.copy(params["text"])
            return {"written": True}