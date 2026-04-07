from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from .models import ClientMessage, ServerMessage, WakeWordConfig
from .session_manager import session_manager
from fastapi import UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from .file_handler import file_handler
from .push_notifications import push_service, PushNotification
from .background_tasks import task_processor
import aiofiles
import uuid

router = APIRouter(prefix="/api/v1", tags=["mobile"])

@router.post("/session/create")
async def create_session(user_id: Optional[str] = None):
    """Create a new session for mobile client"""
    session_id = await session_manager.create_session(user_id or "mobile_user")
    return {"session_id": session_id, "status": "created"}

@router.get("/session/{session_id}/status")
async def get_session_status(session_id: str):
    """Get session status"""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": session.status, "session": session.dict()}

@router.post("/session/{session_id}/command")
async def send_command(session_id: str, message: ClientMessage):
    """Send a command via HTTP (fallback when WebSocket isn't available)"""
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Process synchronously (or queue for async processing)
    # This is a simplified version - WebSocket is recommended
    
    return {"status": "queued", "session_id": session_id}

@router.post("/config/wakeword")
async def configure_wakeword(config: WakeWordConfig):
    """Configure wake word settings (stored per session)"""
    # Store configuration (you can add this to session manager)
    return {"status": "configured", "wake_word": config.wake_word}

@router.post("/upload")
async def upload_file(
    session_id: str = Form(...),
    file: UploadFile = File(...)
):
    """Upload a file for processing"""
    # Process in background
    task_id = await task_processor.submit_task(
        session_id,
        f"Uploading {file.filename}",
        file_handler.upload_file,
        file,
        session_id
    )
    
    return {
        "task_id": task_id,
        "status": "processing",
        "message": f"Uploading {file.filename}"
    }

@router.get("/files/list")
async def list_files(session_id: str):
    """List user's uploaded files"""
    files = await file_handler.list_user_files(session_id)
    return {"files": files}

@router.get("/download/{file_id}")
async def download_file(file_id: str, session_id: str):
    """Download a file"""
    file_path = await file_handler.download_file(file_id, session_id)
    
    if not file_path:
        raise HTTPException(404, "File not found")
    
    # Schedule cleanup of temporary download
    async def cleanup():
        await asyncio.sleep(3600)  # Delete after 1 hour
        if file_path.exists():
            file_path.unlink()
    
    asyncio.create_task(cleanup())
    
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type='application/octet-stream'
    )

@router.post("/notifications/register")
async def register_push_token(session_id: str, fcm_token: str):
    """Register device for push notifications"""
    push_service.register_device(fcm_token)
    
    # Send test notification
    await push_service.send_notification(PushNotification(
        title="Connected ✅",
        body="OpenClaw assistant is ready",
        data={"type": "connected"},
        session_id=session_id
    ))
    
    return {"status": "registered"}

@router.get("/notifications/pending")
async def get_pending_notifications(session_id: str):
    """Get pending notifications for offline user"""
    notifications = await session_manager.get_notifications(session_id)
    return {"notifications": notifications}

@router.post("/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    """Get status of a background task"""
    status = await task_processor.get_task_status(task_id)
    if not status:
        raise HTTPException(404, "Task not found")
    return status

@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a queued task"""
    cancelled = await task_processor.cancel_task(task_id)
    return {"cancelled": cancelled}

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "openclaw-backend"}