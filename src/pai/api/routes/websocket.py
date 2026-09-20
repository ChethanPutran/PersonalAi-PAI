from fastapi import APIRouter
from fastapi import WebSocket
import base64

router = APIRouter()

@router.websocket("/ws/permissions")
async def permission_ws(websocket: WebSocket):
    await websocket.accept()
    # Listen for user approval messages

@router.websocket("/ws/translate")
async def live_translate(ws: WebSocket):
    await ws.accept()
    audio_buffer = b""
    target_lang = "es"
    while True:
        data = await ws.receive_json()
        if data["type"] == "audio":
            audio_buffer += base64.b64decode(data["data"])
            # Process in chunks, run STT -> translation -> TTS -> send back