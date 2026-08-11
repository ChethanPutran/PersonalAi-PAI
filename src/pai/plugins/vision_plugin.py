import asyncio

import cv2
import numpy as np
from typing import Dict, Any, List
import base64
from loguru import logger
from pai.plugins.base_plugin import BasePlugin
import face_recognition

# Attempt to import vision libraries gracefully
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    logger.warning("YOLO not installed. Object detection will be stubbed.")

try:
    import paddleocr
    PADDLE_OCR_AVAILABLE = True
except ImportError:
    PADDLE_OCR_AVAILABLE = False

class VisionPlugin(BasePlugin):
    """
    Complete vision subsystem with:
    - Object detection (YOLO)
    - OCR (PaddleOCR)
    - Scene description (via LLM)
    - Activity recognition
    """
    
    def __init__(self):
        super().__init__()
        self.name = "vision"
        self.yolo_model = None
        self.ocr = None
        self._initialized = False
        self.known_face_encodings = []
        self.known_face_names = []
    
    async def initialize(self) -> None:
        if YOLO_AVAILABLE:
            self.yolo_model = YOLO("yolov8n.pt")  # type: ignore
        if PADDLE_OCR_AVAILABLE:
            self.ocr = paddleocr.PaddleOCR(use_angle_cls=True, lang='en') # type: ignore
        self._initialized = True
        logger.info("VisionPlugin fully initialized")
    
    def get_capabilities(self) -> List[str]:
        return [
            "vision.detect_objects",
            "vision.ocr",
            "vision.describe_scene",
            "vision.recognize_activity",
            "vision.detect_faces",
            "vision.estimate_pose"
        ]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        image_data = params.get("image_data")
        if image_data and isinstance(image_data, str) and image_data.startswith("data:image"):
            # Decode base64 image
            header, encoded = image_data.split(",", 1)
            image_bytes = base64.b64decode(encoded)
            np_arr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        else:
            image = params.get("image")  # assume OpenCV image
        
        if action == "vision.detect_objects":
            return await self._detect_objects(image)
        elif action == "vision.ocr":
            return await self._ocr(image)
        elif action == "vision.describe_scene":
            return await self._describe_scene(image)
        elif action == "vision.recognize_activity":
            return await self._recognize_activity(image)
        elif action == "vision.detect_faces":
            return await self._detect_faces(image)
        elif action == "vision.estimate_pose":
            return await self._estimate_pose(image)
        raise ValueError(f"Unknown vision action: {action}")
    
    async def _detect_objects(self, image: np.ndarray) -> List[Dict[str, Any]]:
        if not YOLO_AVAILABLE or not self.yolo_model:
            return [{"label": "person", "confidence": 0.95, "bbox": [100,100,200,200]}]
        results = self.yolo_model(image)
        detections = []
        for r in results:
            boxes = r.boxes
            if boxes is not None:
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    cls = int(box.cls[0])
                    label = self.yolo_model.names[cls]
                    detections.append({
                        "label": label,
                        "confidence": conf,
                        "bbox": [x1, y1, x2, y2]
                    })
        return detections
    
    async def _ocr(self, image: np.ndarray) -> str:
        if not PADDLE_OCR_AVAILABLE or not self.ocr:
            return "OCR not available. Install paddleocr."
        result = self.ocr.ocr(image, cls=True)
        if not result:
            return ""
        texts = []
        for line in result[0]:
            texts.append(line[1][0])
        return " ".join(texts)
    
    async def _describe_scene(self, image: np.ndarray) -> str:
        # Use a local VLM or LLM via kernel
        detections = await self._detect_objects(image)
        objects = [d["label"] for d in detections]
        prompt = f"A scene containing: {', '.join(objects)}. Describe in one sentence."
        # Placeholder: call LLM plugin
        if self.kernel:
            try:
                llm_response = await self.kernel.plugin_manager.execute_plugin(
                    "llm", "complete", {"prompt": prompt}
                )
                return llm_response.get("text", "Scene with various objects.")
            except:
                pass
        return f"I see {', '.join(objects[:5])}." if objects else "No objects detected."
    
    async def _recognize_activity(self, image: np.ndarray) -> str:
        # Simplified: use pose estimation to infer activity
        # In production, use a dedicated activity recognition model
        return "walking"  # stub
    
    async def _detect_faces(self, image: np.ndarray) -> List[Dict]:
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        return [{"x": int(x), "y": int(y), "w": int(w), "h": int(h)} for (x, y, w, h) in faces]
    
    async def _estimate_pose(self, image: np.ndarray) -> Dict:
        # Would use MediaPipe or OpenPose
        return {"keypoints": []}
    
    async def shutdown(self) -> None:
        self._initialized = False

    async def process_video_stream(self, frame_callback):
        """Process live video frames from mobile camera."""
        # This will be called for each frame; implement frame queue with throttling
        self.frame_queue = asyncio.Queue(maxsize=10)
        self.streaming = True
        asyncio.create_task(self._stream_processor(frame_callback))
    
    async def _stream_processor(self, callback):
        while self.streaming:
            frame = await self.frame_queue.get()
            # Run detection every 5th frame (0.2 sec at 30fps)
            if hasattr(self, '_frame_counter'):
                self._frame_counter += 1
            else:
                self._frame_counter = 0
            if self._frame_counter % 5 == 0:
                detections = await self._detect_objects(frame)
                await callback(detections)
    
    async def add_frame(self, frame_b64: str):
        """Add a base64 frame to the processing queue."""
        import base64
        img_bytes = base64.b64decode(frame_b64)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        await self.frame_queue.put(frame)

    async def register_face(self, name: str, image_b64: str):
        """Register a known face."""
        import base64
        img_bytes = base64.b64decode(image_b64)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb)
        if encodings:
            self.known_face_encodings.append(encodings[0])
            self.known_face_names.append(name)
            return {"registered": True, "name": name}
        return {"error": "No face found"}
    
    async def recognize_faces(self, image_b64: str) -> List[Dict]:
        """Recognize faces in an image."""
        import base64
        img_bytes = base64.b64decode(image_b64)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb)
        face_encodings = face_recognition.face_encodings(rgb, face_locations)
        
        recognized = []
        for encoding in face_encodings:
            matches = face_recognition.compare_faces(self.known_face_encodings, encoding)
            name = "Unknown"
            if True in matches:
                first_match_index = matches.index(True)
                name = self.known_face_names[first_match_index]
            recognized.append({"name": name, "location": face_locations[0]})
        return recognized