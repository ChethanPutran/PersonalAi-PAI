import subprocess
from typing import Any
from pai.plugins.base_plugin import BasePlugin

class IDEPugin(BasePlugin):
    name = "ide"
    
    async def execute(self, action, params)->Any:
        if action == "ide.open_file":
            subprocess.Popen(["code", params["path"]])  # VSCode
            return {"opened": params["path"]}
        elif action == "ide.run_command":
            result = subprocess.run(params["command"], shell=True, capture_output=True, text=True)
            return {"stdout": result.stdout, "stderr": result.stderr}