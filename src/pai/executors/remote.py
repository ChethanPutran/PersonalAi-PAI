"""
Remote executor.

Communicates with a remote PAI executor through HTTP.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from loguru import logger

from pai.executors.base import BaseExecutor


class RemoteExecutor(BaseExecutor):
    """
    HTTP proxy for a remote executor.
    """

    def __init__(
        self,
        executor_id: str,
        executor_data: Dict[str, Any],
    ) -> None:

        super().__init__(
            name=executor_id,
            executor_id=executor_id,
        )

        self.address = executor_data.get("address")
        self.executor_type = executor_data.get(
            "type",
            "remote",
        )

        self.scheme = executor_data.get(
            "scheme",
            "http",
        )

        self.timeout = float(
            executor_data.get(
                "timeout",
                30.0,
            )
        )

        self._client: Optional[httpx.AsyncClient] = None

        if not self.address:
            raise ValueError(
                "Remote executor requires 'address'"
            )

    @property
    def base_url(self) -> str:

        address = self.address.rstrip("/")

        if address.startswith(
            ("http://", "https://")
        ):
            return address

        return f"{self.scheme}://{address}"

    async def initialize(self) -> None:

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )

        self._initialized = True

        logger.info(
            "RemoteExecutor initialized: {}",
            self.base_url,
        )

    async def start(self) -> None:

        if not self._initialized:
            await self.initialize()

        self._running = True

        logger.info(
            "RemoteExecutor started: {}",
            self.executor_id,
        )

    async def execute_task(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:

        if not self.running:
            return {
                "status": "failed",
                "error": "RemoteExecutor is not running",
            }

        if self._client is None:
            raise RuntimeError(
                "Remote executor client not initialized"
            )

        try:
            response = await self._client.post(
                "/execute",
                json=task,
            )

            response.raise_for_status()

            result = response.json()

            return {
                "status": "success",
                "executor": self.name,
                "remote": True,
                "result": result,
            }

        except httpx.HTTPError as exc:

            logger.error(
                "Remote executor request failed: {}",
                exc,
            )

            return {
                "status": "failed",
                "executor": self.name,
                "remote": True,
                "error": str(exc),
            }

    async def health_check(self) -> bool:

        if self._client is None:
            return False

        try:
            response = await self._client.get(
                "/health"
            )

            return response.is_success

        except httpx.HTTPError:
            return False

    async def stop(self) -> None:

        self._running = False

        logger.info(
            "RemoteExecutor stopped: {}",
            self.executor_id,
        )

    async def shutdown(self) -> None:

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        await super().shutdown()