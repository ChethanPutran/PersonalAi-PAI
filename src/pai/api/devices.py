from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, Dict, Any

from pai.app_context import get_app_context, PAIAppContext
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post('/api/v1/devices/register')
async def register_device(request: Request, payload: Dict[str, Any], context: PAIAppContext = Depends(get_app_context)):
    """Register or update a device for a user."""
    try:
        client = request.client.host if request.client else 'unknown'
        logger.info(f"Incoming device.register request from {client}: {payload}")
    except Exception:
        logger.info(f"Incoming device.register request: {payload}")

    user_id = payload.get('user_id')
    device_id = payload.get('device_id')
    if not user_id or not device_id:
        logger.warning("device.register missing user_id or device_id")
        raise HTTPException(status_code=400, detail='user_id and device_id are required')

    pm = context.kernel.plugin_manager
    rec = await pm.register_device(
        user_id=user_id,
        device_id=device_id,
        device_name=payload.get('device_name'),
        device_type=payload.get('device_type'),
        platform=payload.get('platform'),
        capabilities=payload.get('capabilities'),
        config=payload.get('config'),
    )
    if rec is None:
        logger.error(f"Failed to register device {device_id} for user {user_id}")
        raise HTTPException(status_code=500, detail='failed to register device')
    logger.info(f"Device registered: {device_id} for user {user_id}")
    return {'status': 'ok', 'device': await pm.get_device(device_id)}


@router.get('/api/v1/devices/list/{user_id}')
async def list_devices(user_id: str, context: PAIAppContext = Depends(get_app_context)):
    try:
        logger.info(f"Incoming device.list request for user: {user_id}")
    except Exception:
        pass
    pm = context.kernel.plugin_manager
    devices = await pm.list_user_devices(user_id)
    logger.info(f"Devices returned for user {user_id}: {len(devices)}")
    return {'devices': devices}


@router.post('/api/v1/devices/{device_id}/heartbeat')
async def device_heartbeat(device_id: str, payload: Dict[str, Any], context: PAIAppContext = Depends(get_app_context)):
    pm = context.kernel.plugin_manager
    ok = await pm.update_device_heartbeat(device_id, is_online=payload.get('is_online', True))
    if not ok:
        raise HTTPException(status_code=404, detail='device not found')
    return {'status': 'ok'}
