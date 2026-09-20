"""File browsing API with basic security checks."""
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import List, Dict, Any, Optional
from pathlib import Path
import logging

from pydantic import BaseModel

from pai.config import config
from pai.app_context import get_kernel
from pai.kernel.ai_kernel import AIKernel

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/files", tags=["files"])


def _allowed_roots() -> List[Path]:
    # Allow user to configure roots in config; default to current dir and home
    roots = [Path.cwd(), Path.home()]
    env_roots = getattr(config, "file_roots", None)
    if env_roots and isinstance(env_roots, list):
        for r in env_roots:
            try:
                roots.append(Path(r))
            except Exception:
                continue
    # normalize
    return [p.resolve() for p in roots]


def _is_within_roots(path: Path, roots: List[Path]) -> bool:
    try:
        path = path.resolve()
    except Exception:
        return False
    for root in roots:
        try:
            if root in path.parents or path == root:
                return True
        except Exception:
            continue
    return False


@router.get("/list")
async def list_files(path: str = Query("."), user_id: Optional[str] = Query(None), kernel: AIKernel = Depends(get_kernel)) -> Dict[str, Any]:
    """List files and directories under the specified path, constrained to allowed roots."""
    roots = _allowed_roots()
    base = Path(path)
    if not base.is_absolute():
        # interpret relative to current working directory
        base = (Path.cwd() / base)

    if not _is_within_roots(base, roots):
        raise HTTPException(status_code=403, detail="Access to this path is not allowed")

    # Check per-user permissions
    user_id = user_id or getattr(kernel, 'user_id', None)
    if getattr(kernel, 'context_manager', None):
        user_id = getattr(kernel.context_manager, 'user_id', user_id)

    pm = getattr(kernel, 'plugin_manager', None)
    if user_id and pm:
        perms = await pm.list_file_permissions(user_id)
        allowed = False
        requested = base.resolve()
        for p in perms:
            try:
                ppath = Path(p.get('path'))
                if p.get('allowed') and (ppath == requested or ppath in requested.parents):
                    allowed = True
                    break
            except Exception:
                continue
        if not allowed:
            raise HTTPException(status_code=403, detail='No permission for this user to access this path')
    else:
        # deny if there is no user context
        raise HTTPException(status_code=403, detail='User context required')

    if not base.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    entries = []
    try:
        for child in sorted(base.iterdir()):
            entries.append({
                "name": child.name,
                "path": str(child.resolve()),
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            })
    except Exception as e:
        logger.error(f"Error listing files for {base}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"path": str(base.resolve()), "entries": entries}


@router.get("/read")
async def read_file(path: str = Query(...), user_id: Optional[str] = Query(None), kernel: AIKernel = Depends(get_kernel)) -> Dict[str, Any]:
    """Read a file content constrained by allowed roots. Returns text or base64 for binary."""
    roots = _allowed_roots()
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = (Path.cwd() / file_path)

    if not _is_within_roots(file_path, roots):
        raise HTTPException(status_code=403, detail="Access to this path is not allowed")

    # Check per-user permissions
    user_id = user_id or getattr(kernel, 'user_id', None)
    if getattr(kernel, 'context_manager', None):
        user_id = getattr(kernel.context_manager, 'user_id', user_id)

    pm = getattr(kernel, 'plugin_manager', None)
    if user_id and pm:
        perms = await pm.list_file_permissions(user_id)
        allowed = False
        requested = file_path.resolve()
        for p in perms:
            try:
                ppath = Path(p.get('path'))
                if p.get('allowed') and (ppath == requested or ppath in requested.parents):
                    allowed = True
                    break
            except Exception:
                continue
        if not allowed:
            raise HTTPException(status_code=403, detail='No permission for this user to access this file')
    else:
        raise HTTPException(status_code=403, detail='User context required')

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Try to read as text
    try:
        text = file_path.read_text(encoding='utf-8')
        return {"path": str(file_path.resolve()), "type": "text", "content": text}
    except Exception:
        # binary fallback
        try:
            import base64

            data = file_path.read_bytes()
            return {"path": str(file_path.resolve()), "type": "binary", "content_base64": base64.b64encode(data).decode('ascii')}
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            raise HTTPException(status_code=500, detail=str(e))


# Permission APIs


class FileGrantRequest(BaseModel):
    user_id: str
    path: str
    granted_by: Optional[str] = None


class FileRevokeRequest(BaseModel):
    user_id: str
    path: str


class FileRequestAccess(BaseModel):
    user_id: str
    path: str
    requester: Optional[str] = None


@router.post('/permissions/grant')
async def grant_permission(req: FileGrantRequest, kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')

    rec = await pm.grant_file_permission(req.user_id, req.path, granted_by=req.granted_by)
    if rec is None:
        raise HTTPException(status_code=500, detail='Failed to grant permission')
    return {"status": "granted", "id": rec.id, "path": rec.path}


@router.post('/permissions/revoke')
async def revoke_permission(req: FileRevokeRequest, kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    ok = await pm.revoke_file_permission(req.user_id, req.path)
    if not ok:
        raise HTTPException(status_code=404, detail='Permission not found')
    return {"status": "revoked"}


@router.get('/permissions')
async def list_permissions(user_id: Optional[str] = Query(None), kernel: AIKernel = Depends(get_kernel)):
    # default to kernel's current user
    uid = user_id or getattr(kernel, 'user_id', None)
    if not uid:
        raise HTTPException(status_code=400, detail='user_id is required')
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    recs = await pm.list_file_permissions(uid)
    return {"permissions": recs}


@router.post('/request-access')
async def request_access(req: FileRequestAccess = Body(...), kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    rec = await pm.create_file_permission_request(req.user_id, req.path, requester=req.requester)
    if rec is None:
        raise HTTPException(status_code=500, detail='Failed to create request')
    return {"status": "requested", "request_id": rec.id}


@router.get('/permissions/requests')
async def list_requests(user_id: Optional[str] = Query(None), kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    reqs = await pm.list_permission_requests(user_id)
    return {"requests": reqs}


@router.post('/permissions/requests/{request_id}/approve')
async def approve_request(request_id: int, kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    # fetch request to get user_id/path
    reqs = await pm.list_permission_requests(None)
    target = next((r for r in reqs if r['id'] == request_id), None)
    if not target:
        raise HTTPException(status_code=404, detail='Request not found')
    ok = await pm.grant_file_permission(target['user_id'], target['path'], granted_by='system')
    if not ok:
        raise HTTPException(status_code=500, detail='Failed to grant permission')
    await pm.update_permission_request_status(request_id, 'approved')
    return {"status": "approved"}


@router.post('/permissions/requests/{request_id}/deny')
async def deny_request(request_id: int, kernel: AIKernel = Depends(get_kernel)):
    pm = getattr(kernel, 'plugin_manager', None)
    if pm is None:
        raise HTTPException(status_code=500, detail='Plugin manager not available')
    reqs = await pm.list_permission_requests(None)
    target = next((r for r in reqs if r['id'] == request_id), None)
    if not target:
        raise HTTPException(status_code=404, detail='Request not found')
    await pm.update_permission_request_status(request_id, 'denied')
    return {"status": "denied"}
