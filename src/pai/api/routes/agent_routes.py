from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from pai.main import kernel
from pai.api.middleware.auth import get_current_user

router = APIRouter()

@router.post("/{agent_name}/goal")
async def send_agent_goal(agent_name: str, goal_data: Dict[str, Any], user=Depends(get_current_user)):
    result = await kernel.agent_manager.send_goal(agent_name, goal_data.get("goal"), goal_data.get("context", {}))
    return {"agent": agent_name, "result": result}