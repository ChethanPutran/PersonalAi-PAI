from typing import Dict, Any
from loguru import logger
from pai.executors.base_executor import BaseExecutor

class MobileExecutor(BaseExecutor):
    async def initialize(self) -> None:
        logger.info("MobileExecutor initialized")
    
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {"executed_on": "mobile", "task": task}