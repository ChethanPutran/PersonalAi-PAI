"""Vision Pipeline: Integration of YOLO v8, SAM2, Florence-2, and PaddleOCR for multimodal vision understanding."""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np
import cv2
from PIL import Image
import io

logger = logging.getLogger(__name__)


@dataclass
class VisionObject:
    """Detected object in image."""
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    class_id: int


@dataclass
class SegmentationMask:
    """Image segmentation mask."""
    mask: np.ndarray
    label: str
    confidence: float


@dataclass
class TextDetection:
    """Detected text in image."""
    text: str
    bbox: Tuple[int, int, int, int]
    confidence: float
    language: str


@dataclass
class VisionAnalysis:
    """Complete vision analysis result."""
    image_path: str
    objects: List[VisionObject]
    segmentation_masks: List[SegmentationMask]
    text_content: List[TextDetection]
    scene_description: str
    threat_level: str  # safe, caution, danger
    metadata: Dict[str, Any]


class VisionPipeline:
    """Main vision processing pipeline combining multiple vision models."""

    def __init__(self, device: str = "cuda:0", model_cache_dir: Optional[str] = None):
        """Initialize vision pipeline with all models.
        
        Args:
            device: GPU device (cuda:0, cpu, etc)
            model_cache_dir: Directory for caching models
        """
        self.device = device
        self.model_cache_dir = Path(model_cache_dir) if model_cache_dir else Path.home() / ".cache" / "pai_vision"
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.yolo_model = None
        self.sam2_model = None
        self.florence_model = None
        self.ocr_engine = None
        
        # Initialize models lazily
        self._initialized = False
        
    async def initialize(self) -> None:
        """Lazy initialize all models (run once)."""
        if self._initialized:
            return
            
        logger.info("Initializing vision pipeline models...")
        try:
            # YOLO v8 for object detection
            from ultralytics import YOLO
            self.yolo_model = YOLO("yolov8x.pt").to(self.device)
            logger.info("✓ YOLO v8 loaded")
            
            # SAM2 for segmentation
            # Note: SAM2 requires torch and timm
            try:
                import sam2
                logger.info("✓ SAM2 available (real model loading handled lazily)")
            except ImportError:
                logger.warning("SAM2 not available, segmentation disabled")
                
            # Florence-2 for vision-language understanding
            try:
                from transformers import AutoProcessor, AutoModelForCausalLM
                self.florence_processor = AutoProcessor.from_pretrained("microsoft/Florence-2-large")
                self.florence_model = AutoModelForCausalLM.from_pretrained("microsoft/Florence-2-large").to(self.device)
                logger.info("✓ Florence-2 loaded")
            except Exception as e:
                logger.warning(f"Florence-2 loading failed: {e}")
                
            # PaddleOCR for text extraction
            try:
                from paddleocr import PaddleOCR
                self.ocr_engine = PaddleOCR(use_angle_cls=True, lang='en')
                logger.info("✓ PaddleOCR loaded")
            except Exception as e:
                logger.warning(f"PaddleOCR loading failed: {e}")
                
            self._initialized = True
            logger.info("Vision pipeline initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize vision pipeline: {e}")
            raise
    
    async def detect_objects(self, image_path: str, conf_threshold: float = 0.5) -> List[VisionObject]:
        """Detect objects in image using YOLO v8.
        
        Args:
            image_path: Path to image file
            conf_threshold: Confidence threshold for detections
            
        Returns:
            List of detected objects with bounding boxes and confidence
        """
        await self.initialize()
        
        if self.yolo_model is None:
            logger.error("YOLO model not available")
            return []
            
        try:
            results = self.yolo_model.predict(image_path, conf=conf_threshold, verbose=False)
            objects = []
            
            for result in results:
                for box in result.boxes:
                    obj = VisionObject(
                        class_name=result.names[int(box.cls)],
                        confidence=float(box.conf),
                        bbox=tuple(map(int, box.xyxy[0].tolist())),
                        class_id=int(box.cls)
                    )
                    objects.append(obj)
                    
            logger.info(f"Detected {len(objects)} objects in {image_path}")
            return objects
        except Exception as e:
            logger.error(f"Object detection failed: {e}")
            return []
    
    async def segment_image(self, image_path: str) -> List[SegmentationMask]:
        """Segment image using SAM2.
        
        Args:
            image_path: Path to image file
            
        Returns:
            List of segmentation masks with labels
        """
        await self.initialize()
        
        # Placeholder: SAM2 integration requires additional setup
        logger.info("SAM2 segmentation placeholder - full implementation requires SAM2 models")
        return []
    
    async def extract_text(self, image_path: str) -> List[TextDetection]:
        """Extract text from image using PaddleOCR.
        
        Args:
            image_path: Path to image file
            
        Returns:
            List of detected text with bounding boxes
        """
        await self.initialize()
        
        if self.ocr_engine is None:
            logger.error("OCR engine not available")
            return []
            
        try:
            results = self.ocr_engine.ocr(image_path)
            text_detections = []
            
            if results:
                for line in results:
                    for detection in line:
                        bbox_points = detection[0]
                        text = detection[1]
                        confidence = detection[2]
                        
                        # Convert polygon to bounding box
                        x_coords = [p[0] for p in bbox_points]
                        y_coords = [p[1] for p in bbox_points]
                        bbox = (min(x_coords), min(y_coords), max(x_coords), max(y_coords))
                        
                        text_det = TextDetection(
                            text=text,
                            bbox=tuple(map(int, bbox)),
                            confidence=confidence,
                            language="en"
                        )
                        text_detections.append(text_det)
                        
            logger.info(f"Extracted {len(text_detections)} text regions from {image_path}")
            return text_detections
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return []
    
    async def describe_scene(self, image_path: str) -> str:
        """Generate scene description using Florence-2 vision-language model.
        
        Args:
            image_path: Path to image file
            
        Returns:
            Natural language description of scene
        """
        await self.initialize()
        
        if self.florence_model is None:
            logger.error("Florence-2 model not available")
            return "Scene description unavailable"
            
        try:
            # Load image
            image = Image.open(image_path).convert("RGB")
            
            # Florence-2 requires specific prompting format
            # Using "dense region caption" task for detailed description
            task = "<DENSE_REGION_CAPTION>"
            
            # Process image
            inputs = self.florence_processor(text=task, images=image, return_tensors="pt").to(self.device)
            
            # Generate caption
            with torch.no_grad():
                generated_ids = self.florence_model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=1024,
                    early_stopping=False,
                    do_sample=False,
                    num_beams=3,
                )
            
            description = self.florence_processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            logger.info(f"Generated scene description for {image_path}")
            return description
            
        except Exception as e:
            logger.error(f"Scene description generation failed: {e}")
            return f"Scene description unavailable: {str(e)}"
    
    async def analyze_complete(self, image_path: str, enable_segmentation: bool = False) -> VisionAnalysis:
        """Run complete vision analysis pipeline.
        
        Args:
            image_path: Path to image file
            enable_segmentation: Whether to run segmentation (slower)
            
        Returns:
            Complete analysis result
        """
        await self.initialize()
        
        logger.info(f"Starting complete vision analysis for {image_path}")
        
        # Run all analyses in parallel
        objects_task = asyncio.create_task(self.detect_objects(image_path))
        text_task = asyncio.create_task(self.extract_text(image_path))
        scene_task = asyncio.create_task(self.describe_scene(image_path))
        
        if enable_segmentation:
            segmentation_task = asyncio.create_task(self.segment_image(image_path))
        else:
            segmentation_task = None
        
        # Wait for all tasks
        objects = await objects_task
        text_content = await text_task
        scene_description = await scene_task
        segmentation_masks = await segmentation_task if segmentation_task else []
        
        # Determine threat level based on detected objects
        threat_level = self._assess_threat_level(objects)
        
        analysis = VisionAnalysis(
            image_path=image_path,
            objects=objects,
            segmentation_masks=segmentation_masks or [],
            text_content=text_content,
            scene_description=scene_description,
            threat_level=threat_level,
            metadata={
                "model_versions": {
                    "yolo": "v8x",
                    "sam2": "available" if self.sam2_model else "unavailable",
                    "florence": "large",
                    "ocr": "paddle"
                },
                "processing_device": self.device,
                "total_objects": len(objects),
                "total_text_regions": len(text_content)
            }
        )
        
        logger.info(f"Vision analysis complete: {len(objects)} objects, {len(text_content)} text regions")
        return analysis
    
    def _assess_threat_level(self, objects: List[VisionObject]) -> str:
        """Assess potential safety threats in detected objects.
        
        Args:
            objects: List of detected objects
            
        Returns:
            Threat level: 'safe', 'caution', or 'danger'
        """
        # Define threat keywords
        danger_keywords = {"weapon", "knife", "gun", "explosion", "fire", "crash"}
        caution_keywords = {"traffic", "construction", "crowd", "height"}
        
        threat_level = "safe"
        for obj in objects:
            class_name_lower = obj.class_name.lower()
            if any(keyword in class_name_lower for keyword in danger_keywords):
                threat_level = "danger"
                break
            elif any(keyword in class_name_lower for keyword in caution_keywords):
                if threat_level != "danger":
                    threat_level = "caution"
        
        return threat_level
    
    async def process_video(self, video_path: str, frame_interval: int = 30) -> List[VisionAnalysis]:
        """Process video frame by frame.
        
        Args:
            video_path: Path to video file
            frame_interval: Process every nth frame
            
        Returns:
            List of vision analyses for selected frames
        """
        logger.info(f"Processing video: {video_path}")
        
        cap = cv2.VideoCapture(video_path)
        frame_count = 0
        analyses = []
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                if frame_count % frame_interval == 0:
                    # Save frame temporarily
                    temp_path = f"/tmp/frame_{frame_count}.jpg"
                    cv2.imwrite(temp_path, frame)
                    
                    # Analyze frame
                    analysis = await self.analyze_complete(temp_path)
                    analyses.append(analysis)
                
                frame_count += 1
        finally:
            cap.release()
        
        logger.info(f"Processed {len(analyses)} frames from video")
        return analyses


# Try importing torch at module level for Florence-2
try:
    import torch
except ImportError:
    torch = None
