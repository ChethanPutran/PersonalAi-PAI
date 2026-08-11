from fastapi import APIRouter
from pai.main import kernel

router = APIRouter()

@router.get("/")
async def list_plugins():
    return await kernel.plugin_manager.list_plugins()

@router.post("/{plugin_name}/execute")
async def execute_plugin(plugin_name: str, action: str, params: dict):
    result = await kernel.plugin_manager.execute_plugin(plugin_name, action, params)
    return {"plugin": plugin_name, "action": action, "result": result}