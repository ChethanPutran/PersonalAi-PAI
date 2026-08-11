from fastapi import WebSocket
import cv2
import numpy as np
import asyncio
import base64
from fastapi import APIRouter
from pai.main import kernel

router = APIRouter()

@router.websocket("/ws/video")
async def video_stream(ws: WebSocket):
    await ws.accept()
    frame_queue = asyncio.Queue(maxsize=10)
    processing = True
    
    async def processor():
        while processing:
            frame = await frame_queue.get()
            # Run detection every 5th frame
            if hasattr(processor, "counter"):
                processor.counter += 1
            else:
                processor.counter = 0
            if processor.counter % 5 == 0:
                detections = await kernel.plugin_manager.execute_plugin("vision", "detect_objects", {"image": frame})
                await ws.send_json({"type": "detections", "data": detections})
    
    asyncio.create_task(processor())
    try:
        while True:
            data = await ws.receive_json()
            if data["type"] == "frame":
                img_bytes = base64.b64decode(data["image"])
                np_arr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                await frame_queue.put(frame)
    except:
        processing = False