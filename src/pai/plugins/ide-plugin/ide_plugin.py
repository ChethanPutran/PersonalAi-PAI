import subprocess
from typing import Any
from pai.plugins.base_plugin import BasePlugin

class IDEPugin(BasePlugin):
    name = "ide"

    async def initialize(self) -> None:
        pass
    
    async def start(self) -> None:
        self._running = True
        # Implement any startup logic here

    async def shutdown(self) -> None:
        pass
    
    async def execute(self, action, params)->Any:
        if action == "ide.open_file":
            subprocess.Popen(["code", params["path"]])  # VSCode
            return {"opened": params["path"]}
        elif action == "ide.run_command":
            result = subprocess.run(params["command"], shell=True, capture_output=True, text=True)
            return {"stdout": result.stdout, "stderr": result.stderr}

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True

    async def handle_event(self, event: str, data: dict) -> None:
        # Implement your event handling logic here
        pass