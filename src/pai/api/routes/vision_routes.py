from fastapi import WebSocket, WebSocketDisconnect
from pai.main import kernel
from fastapi import APIRouter

router = APIRouter()

@router.websocket("/ws/video")
async def video_stream(websocket: WebSocket):
    await websocket.accept()
    vision_plugin = kernel.plugin_manager._plugins.get("vision")
    if not vision_plugin:
        await websocket.close()
        return
    
    detections = await vision_plugin.execute("get_detections",{})
    await websocket.send_json({"type": "detections", "data": detections})
    
    try:
        while True:
            data = await websocket.receive_json()
            if data["type"] == "frame":
                await vision_plugin.add_frame(data["image"])
    except WebSocketDisconnect:
        vision_plugin.streaming = False