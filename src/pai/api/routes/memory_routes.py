from fastapi import APIRouter, Depends
from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

router = APIRouter()

@router.post("/store")
async def store_memory(memory_type: str, data: dict, kernel: AIKernel = Depends(get_kernel)):
    await kernel.memory_manager.remember(memory_type, data)
    return {"status": "stored"}

@router.get("/recall")
async def recall_memory(memory_type: str, query: str, kernel: AIKernel = Depends(get_kernel)):
    result = await kernel.memory_manager.recall(memory_type, {"text": query})
    return {"memories": result}
