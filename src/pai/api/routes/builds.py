from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from typing import List
from pathlib import Path

from pai.app_context import get_app_context, PAIAppContext
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Root folder for build artifacts (create and populate as needed)
BUILDS_DIR = Path('./data/builds')
BUILDS_DIR.mkdir(parents=True, exist_ok=True)


@router.get('/api/v1/builds', response_model=List[str])
async def list_builds(context: PAIAppContext = Depends(get_app_context)):
    """List available build files for download."""
    files = []
    for p in sorted(BUILDS_DIR.iterdir()):
        if p.is_file():
            files.append(p.name)
    logger.info(f"Builds listed: {len(files)} files")
    return files


@router.get('/api/v1/builds/download/{filename}')
async def download_build(filename: str, context: PAIAppContext = Depends(get_app_context)):
    """Download a named build artifact. Filename is validated to prevent path traversal."""
    safe_path = (BUILDS_DIR / Path(filename).name)
    if not safe_path.exists() or not safe_path.is_file():
        logger.warning(f"Requested build not found: {filename}")
        raise HTTPException(status_code=404, detail='build not found')
    logger.info(f"Serving build: {safe_path}")
    return FileResponse(path=str(safe_path), filename=safe_path.name)
