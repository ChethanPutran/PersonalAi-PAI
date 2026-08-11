from fastapi import APIRouter
from pai.main import kernel

router = APIRouter()

@router.post("/store")
async def store_memory(memory_type: str, data: dict):
    await kernel.memory_manager.remember(memory_type, data)
    return {"status": "stored"}

@router.get("/recall")
async def recall_memory(memory_type: str, query: str):
    result = await kernel.memory_manager.recall(memory_type, {"text": query})
    return {"memories": result}