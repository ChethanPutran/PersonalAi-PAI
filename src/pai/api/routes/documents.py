"""Document index API: devices upload text chunks of files the user allowed; the backend embeds and searches.

A device can only write its OWN chunks (X-PAI-Device-Id must equal the device), and only while the user has
turned "index documents" on in the files plugin settings. Search follows the remote-use rule.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from pai.api.dependencies import get_current_user, get_source_device_id
from pai.app_context import PAIAppContext, get_app_context
from pai.documents.index import Chunk, IndexedFile, IndexError_

router = APIRouter(prefix="/documents", tags=["documents"])


class ChunkIn(BaseModel):
    text: str
    page: Optional[int] = None


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    mtime: float = 0.0
    size: int = 0
    chunks: List[ChunkIn] = Field(default_factory=list)


class IndexRequest(BaseModel):
    device_id: str
    files: List[FileIn]


class RemoveRequest(BaseModel):
    device_id: str
    paths: List[str] = Field(default_factory=list)
    prefix: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    device_id: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=20)


def _service(context: PAIAppContext):
    svc = getattr(context.orchestrator, "documents", None)
    if svc is None:
        raise HTTPException(503, "Document search is not available on this server")
    return svc


async def _owned(context: PAIAppContext, user_id: str, device_id: str) -> None:
    device = await context.orchestrator.device_manager.get(device_id)
    if device is None or getattr(device, "user_id", None) != user_id:
        raise HTTPException(404, f"Device '{device_id}' not found")


@router.post("/index")
async def index_files(payload: IndexRequest, user_id: str = Depends(get_current_user),
                      source_device_id: Optional[str] = Depends(get_source_device_id),
                      context: PAIAppContext = Depends(get_app_context)):
    svc = _service(context)
    await _owned(context, user_id, payload.device_id)
    if source_device_id != payload.device_id:
        raise HTTPException(403, {"code": "device_only",
                                  "message": "Only a device can add its own files to the index."})
    policy = await svc.policy_for(payload.device_id)
    if policy is None:
        raise HTTPException(409, {"code": "indexing_off",
                                  "message": "Turn on 'Index documents' in this device's Files plugin settings first."})
    try:
        return await svc.index.replace_files(
            user_id, payload.device_id,
            [IndexedFile(f.path, f.mtime, f.size, [Chunk(c.text, c.page) for c in f.chunks]) for f in payload.files],
            policy=policy)
    except IndexError_ as exc:
        raise HTTPException(413, {"code": "index_limit", "message": str(exc)}) from exc


@router.post("/remove")
async def remove_files(payload: RemoveRequest, user_id: str = Depends(get_current_user),
                       context: PAIAppContext = Depends(get_app_context)):
    """Remove chunks (exact paths, a folder prefix, or everything of the device). Owner from anywhere:
    deleting your own index data needs no extra switch."""
    svc = _service(context)
    await _owned(context, user_id, payload.device_id)
    return {"removed": await svc.index.remove(user_id, payload.device_id, paths=payload.paths, prefix=payload.prefix)}


@router.get("/status")
async def status(device_id: str, user_id: str = Depends(get_current_user),
                 context: PAIAppContext = Depends(get_app_context)):
    svc = _service(context)
    await _owned(context, user_id, device_id)
    return {**await svc.index.status(user_id, device_id), "indexing_enabled": await svc.policy_for(device_id) is not None}


@router.get("/files")
async def indexed_files(device_id: str, user_id: str = Depends(get_current_user),
                        context: PAIAppContext = Depends(get_app_context)):
    """path -> {mtime, size} already indexed, so a device can send only what changed."""
    svc = _service(context)
    await _owned(context, user_id, device_id)
    return {"device_id": device_id, "files": await svc.index.indexed_files(user_id, device_id)}


@router.post("/search")
async def search(payload: SearchRequest, user_id: str = Depends(get_current_user),
                 source_device_id: Optional[str] = Depends(get_source_device_id),
                 context: PAIAppContext = Depends(get_app_context)):
    svc = _service(context)
    if payload.device_id:
        await _owned(context, user_id, payload.device_id)
    results = await svc.search(user_id, payload.query, source_device_id=source_device_id,
                               limit=payload.limit, device_id=payload.device_id)
    return {"query": payload.query, "results": results}
