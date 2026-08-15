from fastapi import APIRouter, Depends
from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

router = APIRouter()

@router.get("/")
async def list_plugins(kernel: AIKernel = Depends(get_kernel)):
    return await kernel.plugin_manager.list_plugins()

@router.post("/{plugin_name}/execute")
async def execute_plugin(plugin_name: str, action: str, params: dict, kernel: AIKernel = Depends(get_kernel)):
    result = await kernel.plugin_manager.execute_plugin(plugin_name, action, params)
    return {"plugin": plugin_name, "action": action, "result": result}
