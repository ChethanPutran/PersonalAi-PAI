from fastapi import WebSocket, WebSocketDisconnect
import base64
import numpy as np
from loguru import logger
from fastapi import APIRouter

router = APIRouter()

# Use faster‑whisper if available, else fallback
try:
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logger.warning("faster-whisper not installed. Streaming STT disabled.")

@router.websocket("/ws/stt")
async def stt_stream(websocket: WebSocket):
    await websocket.accept()
    if not WHISPER_AVAILABLE:
        await websocket.send_json({"error": "Whisper not available"})
        await websocket.close()
        return
    
    audio_buffer = b""
    sample_rate = 16000
    
    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "audio_chunk":
                chunk = base64.b64decode(data["data"])
                audio_buffer += chunk
                
                # Process every 2 seconds of audio
                if len(audio_buffer) >= sample_rate * 2 * 2:  # 2 seconds, 16-bit PCM
                    # Convert bytes to float32 array (assume 16-bit PCM)
                    audio_int16 = np.frombuffer(audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0
                    segments, _ = model.transcribe(audio_int16, beam_size=5)
                    transcript = " ".join(seg.text for seg in segments)
                    if transcript.strip():
                        await websocket.send_json({"type": "transcript", "text": transcript})
                    audio_buffer = b""  # Clear buffer after processing
            elif data["type"] == "reset":
                audio_buffer = b""
    except WebSocketDisconnect:
        logger.info("STT client disconnected")