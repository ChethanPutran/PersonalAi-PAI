from fastapi import APIRouter, Depends
from typing import Dict, Any
from pai.app_context import get_orchestrator
from pai.api.middleware.auth import get_current_user
from pai.orchestration.orchestrator import TaskOrchestrator
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])



"""
Agent Management API Routes

GET  /api/v1/agents
POST /api/v1/agents/{id}/execute
GET  /api/v1/agents/{id}/status

"""

# Request models
class AgentCreateRequest(BaseModel):
    name: str
    agent_type: str
    description: Optional[str] = None


class TaskExecutionRequest(BaseModel):
    goal: str
    context: Optional[dict] = None


@router.get("")
async def list_agents(orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """List all available agents"""
    agents = []
    for name, agent in orchestrator.agent_manager._agents.items():
        agents.append({
            "id": name,
            "name": name.replace("_", " ").title(),
            "type": name.removesuffix("_agent"),
            "description": f"{name.replace('_', ' ').title()} capabilities",
            "capabilities": agent.get_capabilities(),
            "running": agent._running,
        })
    return {
        "agents": agents,
        "total": len(agents),
    }


@router.post("/{agent_id}/execute")
async def execute_agent(agent_id: str, request: TaskExecutionRequest, orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Execute an agent with a goal"""
    try:
        agent_name = agent_id if agent_id.endswith("_agent") else f"{agent_id}_agent"
        result = await orchestrator.agent_manager.send_goal(agent_name, request.goal, request.context or {})
        return {
            "agent_id": agent_name,
            "goal": request.goal,
            "status": "completed",
            "result": result,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Agent execution failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{agent_id}/status")
async def get_agent_status(agent_id: str, orchestrator: TaskOrchestrator = Depends(get_orchestrator)):
    """Get agent status"""
    agent_name = agent_id if agent_id.endswith("_agent") else f"{agent_id}_agent"
    status = await orchestrator.agent_manager.get_agent_status(agent_name)
    if agent_name not in status:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_name, **status[agent_name]}


@router.post("/{agent_name}/goal")
async def send_agent_goal(
    agent_name: str,
    goal_data: Dict[str, Any],
    user=Depends(get_current_user),
    orchestrator: TaskOrchestrator = Depends(get_orchestrator),
):
    result = await orchestrator.agent_manager.send_goal(agent_name, goal_data.get("goal", ""), goal_data.get("context", {}))
    return {"agent": agent_name, "result": result}
