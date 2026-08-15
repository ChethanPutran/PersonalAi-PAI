import asyncio
from typing import Dict, Any
from loguru import logger

import asyncio
from pai.executors.executors_discovery import ExecutorDiscovery
import os


class ExecutorScheduler:
    """Schedules tasks to executors and monitors execution."""

    def __init__(self,name: str, host: str, port: int, kernel, executors: Dict[str, Any]):
        self._executors = executors  # name -> endpoint/connection
        self.zeroconf = None
        self.listener = None
        self.discovery = None
        self.name = name
        self.host = host
        self.port = port
        self.kernel = kernel


    async def initialize(self) -> None:
        logger.info("ExecutorScheduler initialized")

    
    async def start(self):
        # Optionally disable Zeroconf (useful for CI or when mDNS causes conflicts)
        if os.getenv('DISABLE_ZEROCONF', 'false').lower() == 'true':
            logger.info('Zeroconf discovery disabled via DISABLE_ZEROCONF')
            return

        try:
            self.discovery = ExecutorDiscovery(self.kernel, self.name, self.port)
            await self.discovery.start()
        except Exception as e:
            logger.exception(f'Executor discovery failed: {e} — continuing without discovery')
        # Discover and register executors via mDNS or config

    
    async def schedule_task(self, task: Dict[str, Any], executor: str) -> Dict[str, Any]:
        logger.info(f"Scheduling task {task.get('id')} on {executor}")
        # In real system, send RPC request to executor
        await asyncio.sleep(0.1)  # simulate network
        return {"executor": executor, "task": task, "status": "success"}
    
    async def stop(self) -> None:
        pass

    async def shutdown(self) -> None:
        """Clean up resources."""
        await self.stop()
        logger.info("ExecutorScheduler shutdown complete")