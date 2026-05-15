import base64
import time
from typing import Dict, Any

from fastapi import WebSocket, WebSocketDisconnect

from .background_tasks import task_processor
from .models import ClientMessage, MessageType, ServerMessage, SessionStatus
from .push_notifications import push_service
from .session_manager import session_manager
from .vad import AudioPreprocessor, VoiceActivityDetector


class WebSocketHandler:
    """Handle WebSocket connections for the mobile client."""

    def __init__(self, agent_instance):
        self.agent = agent_instance
        self.active_connections: Dict[str, WebSocket] = {}
        self.vad_instances: Dict[str, VoiceActivityDetector] = {}
        self.audio_buffers: Dict[str, bytearray] = {}

    async def handle_connection(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        await session_manager.register_connection(session_id, websocket)
        await session_manager.update_status(session_id, SessionStatus.ACTIVE)

        try:
            while True:
                data = await websocket.receive_text()
                message = ClientMessage.model_validate_json(data)

                if message.type == MessageType.PING:
                    await self._send_pong(websocket, session_id)
                elif message.type in {MessageType.TEXT, MessageType.VOICE}:
                    await self._process_command(websocket, session_id, message)
                elif message.type == MessageType.COMMAND:
                    await self._handle_special_command(websocket, session_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            self.active_connections.pop(session_id, None)
            await session_manager.unregister_connection(session_id)
            await session_manager.update_status(session_id, SessionStatus.IDLE)

    async def _send_pong(self, websocket: WebSocket, session_id: str):
        response = ServerMessage(
            type=MessageType.PONG,
            content="pong",
            session_id=session_id,
        )
        await websocket.send_text(response.model_dump_json())

    async def _process_command(self, websocket: WebSocket, session_id: str, message: ClientMessage):
        start_time = time.time()
        await session_manager.update_status(session_id, SessionStatus.PROCESSING)

        try:
            response_text = self.agent.run(message.content, thread_id=session_id)
            processing_time = (time.time() - start_time) * 1000
            response = ServerMessage(
                type=MessageType.RESPONSE,
                content=response_text or "Command processed",
                session_id=session_id,
                processing_time_ms=processing_time,
                requires_approval=False,
            )
            await websocket.send_text(response.model_dump_json())
            await session_manager.add_to_history(session_id, message.content, response.content)
        except Exception as exc:
            error_response = ServerMessage(
                type=MessageType.ERROR,
                content=f"Error processing command: {exc}",
                session_id=session_id,
                processing_time_ms=(time.time() - start_time) * 1000,
            )
            await websocket.send_text(error_response.model_dump_json())
        finally:
            await session_manager.update_status(session_id, SessionStatus.ACTIVE)

    async def _handle_special_command(self, websocket: WebSocket, session_id: str, message: ClientMessage):
        if message.content.startswith("approve:"):
            approval_id = message.content.split(":", 1)[1]
            await self._resume_with_approval(session_id, approval_id, True)
        elif message.content.startswith("reject:"):
            approval_id = message.content.split(":", 1)[1]
            await self._resume_with_approval(session_id, approval_id, False)

    async def _resume_with_approval(self, session_id: str, approval_id: str, approved: bool):
        await session_manager.resolve_approval(session_id, approval_id, approved)

    async def handle_voice_with_vad(self, websocket: WebSocket, session_id: str, audio_data: str):
        audio_bytes = base64.b64decode(audio_data)

        if session_id not in self.vad_instances:
            self.vad_instances[session_id] = VoiceActivityDetector(mode=3)
            self.audio_buffers[session_id] = bytearray()

        vad = self.vad_instances[session_id]
        preprocessor = AudioPreprocessor()
        audio_bytes = preprocessor.normalize_volume(audio_bytes)

        frame_size = vad.frame_size * 2
        for i in range(0, len(audio_bytes), frame_size):
            frame = audio_bytes[i : i + frame_size]
            if len(frame) != frame_size:
                continue
            is_speaking, confidence = vad.process_chunk(frame)
            speech_segment = vad.extract_speech_segment(frame)
            if speech_segment:
                await self._process_speech_segment(websocket, session_id, speech_segment)
            if confidence > 0.5:
                await websocket.send_json(
                    {"type": "vad_status", "is_speaking": is_speaking, "confidence": confidence}
                )

    async def _process_speech_segment(self, websocket: WebSocket, session_id: str, audio_bytes: bytes):
        await websocket.send_json({"type": "speech_detected", "message": "Processing speech..."})
        await self._process_command(
            websocket,
            session_id,
            ClientMessage(type=MessageType.VOICE, content="<audio>", session_id=session_id),
        )

    async def handle_file_upload(self, session_id: str, file_data: dict):
        from .file_handler import file_handler

        return await task_processor.submit_task(
            session_id,
            f"Processing {file_data['filename']}",
            file_handler.upload_file,
            file_data["content"],
            session_id,
        )
