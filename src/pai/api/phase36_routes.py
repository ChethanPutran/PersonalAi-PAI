"""API Routes for Phases 3-6: Vision, Memory, Automation, Autonomous Agents, Multimodal."""

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Import new services
try:
    from src.core.vision_pipeline import VisionPipeline, VisionAnalysis
    from src.core.memory_v2 import EnhancedMemoryService, MemoryType
    from src.core.planner import PlanningEngine, ReasoningType
    from src.core.desktop_automation import DesktopAutomation
    from src.core.mobile_automation import MobileAutomation
    from src.core.workflow_engine import WorkflowEngine, WorkflowConfig, WorkflowStep
    from src.agents.autonomous_agents import ResearchAgent, MonitoringAgent, FormAutomationAgent
    from src.core.multimodal_ecosystem import VoiceStreamingService, EdgeAIService, DistributedCognitionService
except ImportError as e:
    logger.warning(f"Some services not available: {e}")


# ==================== PYDANTIC MODELS ====================

class VisionAnalysisRequest(BaseModel):
    image_path: str
    enable_segmentation: bool = False

class MemoryStoreRequest(BaseModel):
    content: str
    memory_type: str  # episodic, semantic, procedural
    tags: Optional[List[str]] = None
    importance: float = 0.5

class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = 10

class PlanRequest(BaseModel):
    goal: str
    context: Optional[str] = None
    reasoning_type: str = "chain_of_thought"

class AutomationActionRequest(BaseModel):
    action: str
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    button: Optional[str] = "left"

class ResearchRequest(BaseModel):
    topic: str
    num_sources: int = 5

class MonitorURLRequest(BaseModel):
    url: str
    check_interval: int = 3600

class VoiceStreamRequest(BaseModel):
    user_id: str
    access_key: Optional[str] = None

class SwarmRequest(BaseModel):
    swarm_type: str
    num_agents: int


# ==================== VISION & MEMORY ROUTER ====================

vision_memory_router = APIRouter(prefix="/api/v3/vision-memory", tags=["Vision & Memory"])

vision_pipeline = None
memory_service = None
planning_engine = None

@vision_memory_router.on_event("startup")
async def init_vision_memory():
    global vision_pipeline, memory_service, planning_engine
    try:
        vision_pipeline = VisionPipeline()
        await vision_pipeline.initialize()
        
        memory_service = EnhancedMemoryService()
        await memory_service.initialize()
        
        planning_engine = PlanningEngine()
        await planning_engine.initialize()
        
        logger.info("Vision, Memory, and Planning services initialized")
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")

@vision_memory_router.post("/analyze-image")
async def analyze_image(request: VisionAnalysisRequest):
    """Analyze image with complete vision pipeline."""
    if not vision_pipeline:
        raise HTTPException(status_code=503, detail="Vision service not available")
    
    try:
        analysis = await vision_pipeline.analyze_complete(
            request.image_path,
            enable_segmentation=request.enable_segmentation
        )
        return {
            "status": "success",
            "objects": [vars(obj) for obj in analysis.objects],
            "text_content": [vars(text) for text in analysis.text_content],
            "scene_description": analysis.scene_description,
            "threat_level": analysis.threat_level,
            "metadata": analysis.metadata
        }
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@vision_memory_router.post("/memory/store")
async def store_memory(request: MemoryStoreRequest):
    """Store a memory entry."""
    if not memory_service:
        raise HTTPException(status_code=503, detail="Memory service not available")
    
    try:
        memory_type = MemoryType[request.memory_type.upper()]
        memory_id = await memory_service.store_memory(
            content=request.content,
            memory_type=memory_type,
            tags=request.tags,
            importance=request.importance
        )
        return {"status": "success", "memory_id": memory_id}
    except Exception as e:
        logger.error(f"Memory storage failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@vision_memory_router.post("/memory/search")
async def search_memories(request: MemorySearchRequest):
    """Search for memories."""
    if not memory_service:
        raise HTTPException(status_code=503, detail="Memory service not available")
    
    try:
        memories = await memory_service.search_memories(request.query, top_k=request.top_k)
        return {
            "status": "success",
            "results": [
                {
                    "id": m.id,
                    "type": m.memory_type.value,
                    "content": m.content,
                    "importance": m.importance,
                    "tags": m.tags
                }
                for m in memories
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@vision_memory_router.get("/memory/context/{query}")
async def get_context(query: str):
    """Get contextual information for query."""
    if not memory_service:
        raise HTTPException(status_code=503, detail="Memory service not available")
    
    try:
        context = await memory_service.get_context(query)
        return {"status": "success", "context": context}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@vision_memory_router.post("/plan")
async def create_plan(request: PlanRequest):
    """Create execution plan for goal."""
    if not planning_engine:
        raise HTTPException(status_code=503, detail="Planning service not available")
    
    try:
        planning_engine.available_capabilities = ["vision", "browser", "speech", "memory"]
        reasoning_type = ReasoningType[request.reasoning_type.upper()]
        plan = await planning_engine.create_plan(
            goal=request.goal,
            context=request.context,
            reasoning_type=reasoning_type
        )
        return {
            "status": "success",
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "steps": len(plan.steps),
            "estimated_duration": plan.total_estimated_duration,
            "summary": planning_engine.get_plan_summary(plan)
        }
    except Exception as e:
        logger.error(f"Plan creation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ==================== AUTOMATION ROUTER ====================

automation_router = APIRouter(prefix="/api/v3/automation", tags=["Automation"])

desktop_auto = None
mobile_auto = None
workflow_engine = None

@automation_router.on_event("startup")
async def init_automation():
    global desktop_auto, mobile_auto, workflow_engine
    try:
        desktop_auto = DesktopAutomation()
        await desktop_auto.initialize()
        
        mobile_auto = MobileAutomation()
        await mobile_auto.initialize()
        
        workflow_engine = WorkflowEngine()
        logger.info("Automation services initialized")
    except Exception as e:
        logger.error(f"Failed to initialize automation: {e}")

@automation_router.post("/desktop/action")
async def desktop_action(request: AutomationActionRequest):
    """Perform desktop automation action."""
    if not desktop_auto:
        raise HTTPException(status_code=503, detail="Desktop automation not available")
    
    try:
        if request.action == "click":
            await desktop_auto.click(request.x, request.y)
        elif request.action == "move":
            await desktop_auto.move_mouse(request.x, request.y)
        elif request.action == "type":
            await desktop_auto.type_text(request.text)
        elif request.action == "screenshot":
            img = await desktop_auto.screenshot()
            return {"status": "success", "screenshot_taken": img is not None}
        else:
            raise ValueError(f"Unknown action: {request.action}")
        
        return {"status": "success", "action": request.action}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@automation_router.get("/desktop/devices")
async def get_mobile_devices():
    """Get connected mobile devices."""
    if not mobile_auto:
        raise HTTPException(status_code=503, detail="Mobile automation not available")
    
    try:
        devices = await mobile_auto.get_devices()
        return {
            "status": "success",
            "devices": [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "model": d.model,
                    "status": d.status
                }
                for d in devices
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@automation_router.post("/mobile/tap")
async def mobile_tap(device_id: str, x: int, y: int):
    """Tap on mobile device."""
    if not mobile_auto:
        raise HTTPException(status_code=503, detail="Mobile automation not available")
    
    try:
        await mobile_auto.select_device(device_id)
        success = await mobile_auto.tap(x, y)
        return {"status": "success" if success else "failed", "action": "tap"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==================== AUTONOMOUS AGENTS ROUTER ====================

autonomous_router = APIRouter(prefix="/api/v3/autonomous", tags=["Autonomous Agents"])

research_agent = None
monitoring_agent = None
form_agent = None

@autonomous_router.on_event("startup")
async def init_autonomous():
    global research_agent, monitoring_agent, form_agent
    try:
        research_agent = ResearchAgent()
        await research_agent.initialize()
        
        monitoring_agent = MonitoringAgent()
        await monitoring_agent.initialize()
        
        form_agent = FormAutomationAgent()
        await form_agent.initialize()
        
        logger.info("Autonomous agents initialized")
    except Exception as e:
        logger.error(f"Failed to initialize autonomous agents: {e}")

@autonomous_router.post("/research")
async def research(request: ResearchRequest):
    """Research a topic."""
    if not research_agent:
        raise HTTPException(status_code=503, detail="Research agent not available")
    
    try:
        result = await research_agent.research_topic(request.topic, request.num_sources)
        return {
            "status": "success",
            "query": result.query,
            "sources_found": len(result.sources),
            "summary": result.summary[:200],
            "key_findings": result.key_findings
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@autonomous_router.post("/monitor/add")
async def add_monitor(request: MonitorURLRequest):
    """Add URL to monitoring."""
    if not monitoring_agent:
        raise HTTPException(status_code=503, detail="Monitoring agent not available")
    
    try:
        await monitoring_agent.add_monitor(request.url, request.check_interval)
        return {"status": "success", "url": request.url, "monitoring": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@autonomous_router.post("/monitor/check")
async def check_changes(url: Optional[str] = None):
    """Check for changes in monitored URLs."""
    if not monitoring_agent:
        raise HTTPException(status_code=503, detail="Monitoring agent not available")
    
    try:
        changes = await monitoring_agent.check_for_changes(url)
        return {
            "status": "success",
            "changes_detected": len(changes),
            "changes": [
                {"url": c.url, "changed": c.changed, "changes": c.changes}
                for c in changes
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@autonomous_router.get("/monitor/status")
async def monitor_status():
    """Get monitoring status."""
    if not monitoring_agent:
        raise HTTPException(status_code=503, detail="Monitoring agent not available")
    
    return {"status": "success", "monitoring": monitoring_agent.get_monitoring_status()}


# ==================== MULTIMODAL ROUTER ====================

multimodal_router = APIRouter(prefix="/api/v3/multimodal", tags=["Multimodal"])

voice_streaming = None
edge_ai = None
distributed_cognition = None

@multimodal_router.on_event("startup")
async def init_multimodal():
    global voice_streaming, edge_ai, distributed_cognition
    try:
        voice_streaming = VoiceStreamingService()
        await voice_streaming.initialize()
        
        edge_ai = EdgeAIService()
        await edge_ai.initialize()
        
        distributed_cognition = DistributedCognitionService()
        await distributed_cognition.initialize()
        
        logger.info("Multimodal services initialized")
    except Exception as e:
        logger.error(f"Failed to initialize multimodal services: {e}")

@multimodal_router.post("/voice/stream/create")
async def create_voice_stream(request: VoiceStreamRequest):
    """Create voice stream."""
    if not voice_streaming:
        raise HTTPException(status_code=503, detail="Voice streaming not available")
    
    try:
        stream_id = await voice_streaming.create_voice_stream(request.user_id)
        return {"status": "success", "stream_id": stream_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@multimodal_router.post("/edge-ai/optimize")
async def optimize_model(model_path: str, precision: str = "fp16"):
    """Optimize model for Jetson."""
    if not edge_ai:
        raise HTTPException(status_code=503, detail="Edge AI not available")
    
    try:
        optimized = await edge_ai.optimize_model_for_jetson(
            model_path, 
            input_shape=(1, 3, 224, 224),
            precision=precision
        )
        return {
            "status": "success" if optimized else "failed",
            "optimized_model": optimized
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@multimodal_router.get("/edge-ai/stats")
async def get_edge_stats():
    """Get edge device stats."""
    if not edge_ai:
        raise HTTPException(status_code=503, detail="Edge AI not available")
    
    stats = await edge_ai.get_device_stats()
    return {"status": "success", "stats": stats}

@multimodal_router.post("/swarm/create")
async def create_swarm(request: SwarmRequest):
    """Create agent swarm."""
    if not distributed_cognition:
        raise HTTPException(status_code=503, detail="Distributed cognition not available")
    
    try:
        swarm_id = await distributed_cognition.create_swarm(
            request.swarm_type,
            request.num_agents
        )
        return {"status": "success", "swarm_id": swarm_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Create router list for main app
def get_phase_36_routers():
    """Get all Phase 3-6 routers."""
    return [
        vision_memory_router,
        automation_router,
        autonomous_router,
        multimodal_router
    ]
