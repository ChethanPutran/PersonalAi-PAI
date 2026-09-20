"""
Server-side executor.
"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from pai.executors.base import BaseExecutor


class ServerExecutor(BaseExecutor):
    """
    Executor for server-side operations.

    Typical tasks:
    - LLM reasoning
    - semantic search
    - memory operations
    - heavy computation
    """

    def __init__(self, kernel: Any = None) -> None:
        super().__init__(
            name="server_executor",
            kernel=kernel,
        )

    async def initialize(self) -> None:
        self._initialized = True
        logger.info("ServerExecutor initialized")

    async def start(self) -> None:
        if not self._initialized:
            await self.initialize()

        self._running = True
        logger.info("ServerExecutor started")

    async def execute_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if not self.running:
            return {
                "status": "failed",
                "error": "ServerExecutor is not running",
            }

        task_type = task.get("type")

        if not task_type:
            return {
                "status": "failed",
                "error": "Task type is required",
            }

        handlers = {
            "llm_reason": self._llm_reason,
            "search": self._search,
        }

        handler = handlers.get(task_type)

        if handler is None:
            return await self._generic_task(task)

        try:
            result = await handler(task)

            return {
                "status": "success",
                "executor": self.name,
                "task_type": task_type,
                "result": result,
            }

        except Exception as exc:
            logger.exception(
                "Server task failed: {}",
                task_type,
            )

            return {
                "status": "failed",
                "executor": self.name,
                "task_type": task_type,
                "error": str(exc),
            }

    async def _llm_reason(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if self.kernel is None:
            raise RuntimeError("Kernel is not available")

        llm = getattr(self.kernel, "llm", None)

        if llm is None:
            raise RuntimeError("LLM service is not available")

        prompt = task.get("prompt") or task.get("goal", "")

        if not prompt:
            raise ValueError("llm_reason requires 'prompt' or 'goal'")

        response = await llm.complete(
            prompt,
            model=task.get("model"),
            temperature=task.get("temperature", 0.7),
            max_tokens=task.get("max_tokens", 1000),
        )

        return {
            "response": response,
        }

    async def _search(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        query = task.get("query", "")

        if not query:
            raise ValueError("search requires 'query'")

        # Search integration can later be connected to your
        # plugin manager / search service.
        return {
            "query": query,
            "results": [],
        }

    async def _generic_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        logger.debug(
            "ServerExecutor received generic task: {}",
            task.get("type"),
        )

        return {
            "message": "Task received but no specialized handler exists",
            "task": task,
        }

    async def shutdown(self) -> None:
        logger.info("ServerExecutor shutting down")
        await super().shutdown()