"""Server-side executor."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict

from loguru import logger

from pai.devices.manager import DeviceManager
from pai.executors.base import BaseExecutor
from pai.observability.tracer import get_tracer


class ServerExecutor(BaseExecutor):
    """
    Executor for server-side operations:
      - LLM reasoning (`llm_reason`)
      - Semantic search (`search`)
      - Fall-through for unrecognized tasks (returns a safe no-op)
    """

    def __init__(self, device_manager: DeviceManager,llm_service: Any = None) -> None:
        super().__init__(
            name="server_executor",
            executor_id=str(uuid.uuid4()),
        )
        self.llm_service = llm_service
        self.device_manager = device_manager
        self._pending: Dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_dict(task: Any) -> Dict[str, Any]:
        if task is None:
            return {}
        if isinstance(task, dict):
            return task
        if hasattr(task, "model_dump"):
            return task.model_dump()
        if hasattr(task, "to_dict"):
            return task.to_dict()
        if hasattr(task, "__dict__"):
            return dict(task.__dict__)
        return {}

    @staticmethod
    def _device_identifier() -> str:
        return "server"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        tracer = get_tracer()
        async with tracer.span("executor.server.initialize") as span:
            self._initialized = True
            span.set_attribute("has_llm_service", self.llm_service is not None)
            logger.info("ServerExecutor initialized")

    async def start(self) -> None:
        if not self._initialized:
            await self.initialize()
        self._running = True
        logger.info("ServerExecutor started")

    async def invoke(
        self,
        device_id: str,
        capability: str,
        parameters: Dict[str, Any],
        timeout: float = 60.0,
    ) -> Any:
        """
        Send a capability call to `device_id` over the WS and wait for
        the matching result message. Raises asyncio.TimeoutError if the
        device doesn't reply in `timeout` seconds.
        """
        request_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self.device_manager.send(device_id, {
                "type": "task",
                "request_id": request_id,
                "capability": capability,
                "parameters": parameters,
            })
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(request_id, None)

    def deliver_result(self, request_id: str, result: Any) -> None:
        fut = self._pending.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result(result)

    # ------------------------------------------------------------------
    # Task dispatch
    # ------------------------------------------------------------------

    async def execute_task(self, task: Any) -> Dict[str, Any]:
        task_dict = self._as_dict(task)
        task_type = task_dict.get("type")
        capability = task_dict.get("capability") or task_type

        tracer = get_tracer()
        async with tracer.span(
            "executor.server.execute_task",
            task_type=task_type,
            capability=capability,
        ) as span:

            if not self.running:
                span.set_error("executor_not_running")
                return {
                    "status": "failed",
                    "executor": self.name,
                    "task_type": task_type,
                    "error": "ServerExecutor is not running",
                }

            if not task_type:
                span.set_error("missing_task_type")
                return {
                    "status": "failed",
                    "executor": self.name,
                    "error": "Task type is required",
                }

            handlers = {
                "llm_reason": self._llm_reason,
                "search":     self._search,
            }

            handler = handlers.get(task_type)
            if handler is None:
                # Generic tasks go through `_generic_task`, which returns a
                # "task received but no handler" payload — not an error.
                return await self._generic_task(task_dict)

            params = task_dict.get("parameters") or task_dict
            tracer.log_tool_call(
                capability=capability or task_type,
                plugin=f"executor:{self.name}",
                device_id=self._device_identifier(),
                parameters=params,
            )

            t0 = time.perf_counter()
            try:
                result = await handler(task_dict)
                duration_ms = (time.perf_counter() - t0) * 1000

                tracer.log_tool_call(
                    capability=capability or task_type,
                    plugin=f"executor:{self.name}",
                    device_id=self._device_identifier(),
                    result=result,
                    duration_ms=duration_ms,
                )
                span.set_attribute("duration_ms", round(duration_ms, 1))

                return {
                    "status": "success",
                    "executor": self.name,
                    "task_type": task_type,
                    "result": result,
                }

            except Exception as exc:
                duration_ms = (time.perf_counter() - t0) * 1000
                logger.exception("Server task failed: {}", task_type)

                tracer.log_tool_call(
                    capability=capability or task_type,
                    plugin=f"executor:{self.name}",
                    device_id=self._device_identifier(),
                    error=str(exc),
                    duration_ms=duration_ms,
                )
                span.set_error(f"{type(exc).__name__}: {exc}")

                return {
                    "status": "failed",
                    "executor": self.name,
                    "task_type": task_type,
                    "error": str(exc),
                }

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _llm_reason(self, task: Dict[str, Any]) -> Dict[str, Any]:
        if self.llm_service is None:
            raise RuntimeError("LLM service is not available")

        # Accept either `parameters.prompt` (from the DAG) or a top-level
        # `prompt`/`goal` (from ad-hoc invocation).
        params = task.get("parameters") or {}
        prompt = (
            params.get("prompt")
            or params.get("goal")
            or task.get("prompt")
            or task.get("goal")
            or ""
        )
        if not prompt:
            raise ValueError("llm_reason requires 'prompt' or 'goal'")

        response = await self.llm_service.complete(
            prompt,
            model=params.get("model") or task.get("model"),
            temperature=params.get("temperature", task.get("temperature", 0.7)),
            max_tokens=params.get("max_tokens", task.get("max_tokens", 1000)),
        )

        return {"response": response}

    async def _search(self, task: Dict[str, Any]) -> Dict[str, Any]:
        params = task.get("parameters") or {}
        query = params.get("query") or task.get("query", "")
        if not query:
            raise ValueError("search requires 'query'")

        # Integration point — hook to your search service / vector store.
        return {"query": query, "results": []}

    async def _generic_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(
            "ServerExecutor received generic task: {}", task.get("type"),
        )
        return {
            "status": "success",
            "executor": self.name,
            "task_type": task.get("type"),
            "result": {
                "message": "Task received; no specialized handler exists",
                "task": task,
            },
        }

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        logger.info("ServerExecutor shutting down")
        await super().shutdown()