import asyncio
import time
from typing import Dict, Any
import json
from fastapi import WebSocket, WebSocketDisconnect
from .session_manager import session_manager
from .models import ClientMessage, ServerMessage, MessageType, SessionStatus
import asyncio
import base64
from .vad import VoiceActivityDetector, AudioPreprocessor
from .push_notifications import push_service
from .background_tasks import task_processor

class WebSocketHandler:
    """
    Handles WebSocket connections from mobile app
    Communicates with your existing OpenClaw core
    """
    
    def __init__(self, agent_instance):
        """
        Args:
            agent_instance: Your existing OpenClawGraph instance
        """
        self.agent = agent_instance
        self.active_connections: Dict[str, WebSocket] = {}

        self.vad_instances: Dict[str, VoiceActivityDetector] = {}
        self.audio_buffers: Dict[str, bytearray] = {}
        
        # Start background task processor
        asyncio.create_task(task_processor.start())
    
    async def handle_connection(self, websocket: WebSocket, session_id: str):
        """Handle a new WebSocket connection"""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        await session_manager.register_connection(session_id, websocket)
        
        # Update session status
        await session_manager.update_status(session_id, SessionStatus.ACTIVE)
        
        try:
            while True:
                # Receive message from mobile app
                data = await websocket.receive_text()
                message = ClientMessage.parse_raw(data)
                
                # Handle different message types
                if message.type == MessageType.PING:
                    await self._send_pong(websocket, session_id)
                
                elif message.type == MessageType.TEXT or message.type == MessageType.VOICE:
                    # Process the command
                    await self._process_command(websocket, session_id, message)
                
                elif message.type == MessageType.COMMAND:
                    # Special commands (approve, reject, etc.)
                    await self._handle_special_command(websocket, session_id, message)
                
        except WebSocketDisconnect:
            print(f"Client {session_id} disconnected")
        finally:
            del self.active_connections[session_id]
            await session_manager.unregister_connection(session_id)
            await session_manager.update_status(session_id, SessionStatus.IDLE)
    
    async def _send_pong(self, websocket: WebSocket, session_id: str):
        """Send pong response for heartbeat"""
        response = ServerMessage(
            type=MessageType.PONG,
            content="pong",
            session_id=session_id
        )
        await websocket.send_text(response.json())
    
    async def _process_command(self, websocket: WebSocket, session_id: str, message: ClientMessage):
        """Process a user command through your existing agent"""
        start_time = time.time()
        
        # Update session status
        await session_manager.update_status(session_id, SessionStatus.PROCESSING)
        
        try:
            # Call your existing OpenClaw agent (UNCHANGED)
            response_text = self.agent.run(message.content, thread_id=session_id)
            
            processing_time = (time.time() - start_time) * 1000
            
            # Send response back to mobile
            response = ServerMessage(
                type=MessageType.RESPONSE,
                content=response_text if response_text else "Command processed",
                session_id=session_id,
                processing_time_ms=processing_time,
                requires_approval=False
            )
            await websocket.send_text(response.json())
            
            # Store in conversation history
            await session_manager.add_to_history(session_id, message.content, response.content)
            
        except Exception as e:
            # Handle errors
            error_response = ServerMessage(
                type=MessageType.ERROR,
                content=f"Error processing command: {str(e)}",
                session_id=session_id,
                processing_time_ms=(time.time() - start_time) * 1000
            )
            await websocket.send_text(error_response.json())
        
        finally:
            # Update session status back to active
            await session_manager.update_status(session_id, SessionStatus.ACTIVE)
    
    async def _handle_special_command(self, websocket: WebSocket, session_id: str, message: ClientMessage):
        """Handle special commands like approval responses"""
        if message.content.startswith("approve:"):
            approval_id = message.content.split(":")[1]
            # Resume execution with approval
            # This would integrate with your agent's human-in-the-loop
            await self._resume_with_approval(session_id, approval_id, True)
        
        elif message.content.startswith("reject:"):
            approval_id = message.content.split(":")[1]
            await self._resume_with_approval(session_id, approval_id, False)
    
    async def _resume_with_approval(self, session_id: str, approval_id: str, approved: bool):
        """Resume agent execution after approval"""
        # This would need to integrate with your agent's checkpoint system
        # For now, just resolve the approval
        await session_manager.resolve_approval(session_id, approval_id, approved)
        
        # Notify the agent (implementation depends on your agent's interrupt system)
        # You might need to add a small hook in your agent for this
        pass

        async def handle_voice_with_vad(self, websocket: WebSocket, session_id: str, audio_data: str):
        """Handle voice input with VAD"""
        # Decode base64 audio
        audio_bytes = base64.b64decode(audio_data)
        
        # Get or create VAD instance for session
        if session_id not in self.vad_instances:
            self.vad_instances[session_id] = VoiceActivityDetector(mode=3)
            self.audio_buffers[session_id] = bytearray()
        
        vad = self.vad_instances[session_id]
        
        # Preprocess audio
        preprocessor = AudioPreprocessor()
        
        # Assuming 16kHz mono input from mobile
        # Convert if needed (mobile should send 16kHz PCM)
        audio_bytes = preprocessor.normalize_volume(audio_bytes)
        
        # Process in frames (30ms each)
        frame_size = vad.frame_size * 2  # 2 bytes per sample
        for i in range(0, len(audio_bytes), frame_size):
            frame = audio_bytes[i:i+frame_size]
            if len(frame) == frame_size:
                is_speaking, confidence = vad.process_chunk(frame)
                
                # Extract speech segment if available
                speech_segment = vad.extract_speech_segment(frame)
                if speech_segment:
                    # Got a complete speech segment
                    await self._process_speech_segment(websocket, session_id, speech_segment)
                
                # Send VAD status to mobile (optional)
                if confidence > 0.5:
                    await websocket.send_json({
                        "type": "vad_status",
                        "is_speaking": is_speaking,
                        "confidence": confidence
                    })
    
    async def _process_speech_segment(self, websocket: WebSocket, session_id: str, audio_bytes: bytes):
        """Process a complete speech segment"""
        # Convert speech to text (you'd integrate with a STT service)
        # For now, assuming mobile sends text directly
        
        # Send acknowledgment
        await websocket.send_json({
            "type": "speech_detected",
            "message": "Processing speech..."
        })
        
        # Process through agent (async)
        # This could be a background task
        pass
    
    async def handle_file_upload(self, session_id: str, file_data: dict):
        """Handle file upload via WebSocket"""
        from .file_handler import file_handler
        
        # Process file upload in background
        task_id = await task_processor.submit_task(
            session_id,
            f"Processing {file_data['filename']}",
            file_handler.upload_file,
            file_data['content'],
            session_id
        )
        
        return {"task_id": task_id, "status": "processing"}