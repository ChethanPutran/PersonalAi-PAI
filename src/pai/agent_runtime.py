"""Embedded device-agent runtime.

Runs the PAI DeviceAgent as a background task inside the backend's own
event loop so a single process serves both the API and acts as a device.

Enabled when:
  • `PAI_EMBED_AGENT=true` (explicit), or
  • the three required env vars are present
    (`PAI_BACKEND_URL`, `PAI_EMAIL`, `PAI_PASSWORD`)

Lifecycle
---------
start()  — spawns a supervised task. Delays briefly so uvicorn has time
           to bind its listening socket before the agent's first HTTP
           request. If the agent crashes, it restarts with backoff.
stop()   — sets a stop flag, calls `agent.stop()` if present, waits up
           to STOP_TIMEOUT, then cancels the task.

Timing note
-----------
uvicorn binds its socket *after* the lifespan `startup` returns. The
agent must therefore not fire its first HTTP login before that bind
completes — hence `_INITIAL_DELAY`. If you later modify the agent to
retry its initial login, this delay can be dropped to 0.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_INITIAL_DELAY = 3.0
_STOP_TIMEOUT = 8.0
_RESTART_BACKOFF = (2.0, 5.0, 10.0, 30.0)


class EmbeddedAgent:
    def __init__(
        self,
        *,
        initial_delay: float = _INITIAL_DELAY,
        stop_timeout: float = _STOP_TIMEOUT,
    ) -> None:
        self._task: Optional[asyncio.Task] = None
        self._agent = None
        self._initial_delay = initial_delay
        self._stop_timeout = stop_timeout
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    @staticmethod
    def is_enabled() -> bool:
        flag = os.environ.get("PAI_EMBED_AGENT", "").strip().lower()
        if flag in ("1", "true", "yes", "on"):
            return True
        if flag in ("0", "false", "no", "off"):
            return False
        # Auto-detect: run if the credentials are present
        return all(os.environ.get(k) for k in (
            "PAI_BACKEND_URL", "PAI_EMAIL", "PAI_PASSWORD",
        ))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.is_enabled():
            logger.info(
                "Embedded agent disabled "
                "(PAI_EMBED_AGENT not set and credentials missing)"
            )
            return

        try:
            from pai.server.agent.agent import DeviceAgent
            from pai.server.config import DeviceConfig
        except ImportError as exc:
            logger.warning("Embedded agent: cannot import server: %s", exc)
            return

        try:
            cfg = DeviceConfig.from_env()
        except RuntimeError as exc:
            logger.warning(
                "Embedded agent: config error: %s — skipping start", exc,
            )
            return

        self._task = asyncio.create_task(
            self._supervised_run(cfg, DeviceAgent),
            name="embedded_agent",
        )
        logger.info(
            "Embedded agent starting "
            "(backend=%s, delay=%.1fs)",
            cfg.backend_url, self._initial_delay,
        )

    async def _supervised_run(self, cfg, agent_cls) -> None:
        # Give uvicorn time to bind its socket.
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=self._initial_delay,
            )
            return  # stop requested before we even started
        except asyncio.TimeoutError:
            pass

        attempt = 0
        while not self._stop_event.is_set():
            agent = agent_cls(cfg)
            self._agent = agent
            try:
                logger.info(
                    "Embedded agent: starting (attempt %d)", attempt + 1,
                )
                await agent.run()
                logger.info("Embedded agent: exited cleanly")
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Embedded agent crashed")
                attempt += 1
                if attempt >= len(_RESTART_BACKOFF):
                    logger.error(
                        "Embedded agent: giving up after %d restarts",
                        attempt,
                    )
                    return
                delay = _RESTART_BACKOFF[attempt - 1]
                logger.info("Embedded agent: restarting in %.1fs", delay)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=delay,
                    )
                    return  # stop requested during backoff
                except asyncio.TimeoutError:
                    pass

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()

        # Graceful stop if the agent exposes one
        if self._agent is not None and hasattr(self._agent, "stop"):
            try:
                result = self._agent.stop()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Embedded agent: stop() raised")

        try:
            await asyncio.wait_for(
                self._task, timeout=self._stop_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Embedded agent did not stop in %.1fs — cancelling",
                self._stop_timeout,
            )
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            logger.exception("Embedded agent: stop wait failed")

        self._task = None
        self._agent = None
        logger.info("Embedded agent stopped")