"""TurnRunner backed by the real orchestrator (planning, tools, memory, streamed reply)."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Optional

from loguru import logger

from pai.event_bus.events import Event
from pai.orchestration.streaming import ReplySink
from pai.storage.repositories.chat import ChatRepository
from pai.voice.session import StatusCallback, TurnOutcome

TURN_TIMEOUT_SECONDS = 120.0
_TIMEOUT_REPLY = "Sorry, that is taking too long. Please try again."
_ERROR_REPLY = "Sorry, something went wrong with that request."


class OrchestratorTurnRunner:
    def __init__(self, orchestrator: Any, database: Any, *, timeout: float = TURN_TIMEOUT_SECONDS) -> None:
        self.orchestrator = orchestrator
        self.database = database
        self.timeout = timeout

    # ------------------------------------------------------------------ chat session

    async def ensure_chat_session(self, user_id: str, session_id: str) -> None:
        """Create/validate the chat session (raises PermissionError if it is someone else's)."""
        async with self.database.session() as db:
            await ChatRepository(db).get_or_create_session(
                user_id, session_id, title="Voice conversation")
            await db.commit()

    async def _append(self, session_id: str, role: str, content: str, **kw: Any) -> None:
        try:
            async with self.database.session() as db:
                await ChatRepository(db).append_message(session_id, role, content, **kw)
                await db.commit()
        except Exception as exc:                        # history is best-effort; never break the call
            logger.warning("voice: could not persist {} message: {}", role, exc)

    # --------------------------------------------------------------------- the turn

    async def run_turn(
        self, *, user_id: str, session_id: str, device_id: Optional[str], text: str,
        turn_id: str, sink: ReplySink, on_status: StatusCallback,
    ) -> TurnOutcome:
        await self._append(session_id, "user", text)

        async def _on_bus(event: Event) -> None:
            if event.data.get("request_id") == turn_id and event.data.get("stage"):
                await on_status(str(event.data["stage"]), str(event.data.get("message", "")))

        bus = self.orchestrator.event_bus
        sub_id = await bus.subscribe("chat.status", _on_bus)
        try:
            result = await asyncio.wait_for(
                self.orchestrator.run(
                    user_id=user_id, goal=text, session_id=session_id, source_device_id=device_id,
                    request_id=turn_id, reply_sink=sink,
                    context={"mode": "voice", "device_id": device_id},
                ),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            await self._append(session_id, "assistant", _TIMEOUT_REPLY, status="failed")
            return TurnOutcome(reply=_TIMEOUT_REPLY, status="failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("voice turn failed: {}", exc)
            await self._append(session_id, "assistant", _ERROR_REPLY, status="failed")
            return TurnOutcome(reply=_ERROR_REPLY, status="failed")
        finally:
            with contextlib.suppress(Exception):
                await bus.unsubscribe(sub_id)

        status = str(result.get("status", "completed"))
        replies = [r.get("reply") for r in (result.get("results") or [])
                   if isinstance(r, dict) and r.get("reply")]
        reply = "\n".join(replies) or ("Done." if status == "completed" else "I couldn't complete that.")
        await self._append(session_id, "assistant", reply, status=status, plan_id=result.get("plan_id"))
        return TurnOutcome(reply=reply, status=status)

    async def on_interrupted(
        self, *, user_id: str, session_id: str, turn_id: str, spoken_text: str,
    ) -> None:
        """Cut off mid-reply: history keeps only what the user actually heard."""
        await self._append(
            session_id, "assistant", spoken_text or "(interrupted)", status="interrupted",
            metadata={"interrupted": True, "turn_id": turn_id})
