from fastapi import APIRouter, Depends
from typing import Dict, Any
from pai.app_context import get_kernel
from pai.api.middleware.auth import get_current_user
from pai.kernel.ai_kernel import AIKernel

router = APIRouter()

@router.post("/{agent_name}/goal")
async def send_agent_goal(
    agent_name: str,
    goal_data: Dict[str, Any],
    user=Depends(get_current_user),
    kernel: AIKernel = Depends(get_kernel),
):
    result = await kernel.agent_manager.send_goal(agent_name, goal_data.get("goal", ""), goal_data.get("context", {}))
    return {"agent": agent_name, "result": result}
