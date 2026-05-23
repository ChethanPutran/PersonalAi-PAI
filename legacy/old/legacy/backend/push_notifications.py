import json
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import aiohttp
import os
from dataclasses import dataclass
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class PushNotification:
    title: str
    body: str
    data: Dict[str, Any]
    session_id: str
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

class PushNotificationService:
    """
    Push notification service supporting FCM (Firebase) and APNS (Apple)
    For single user - stores FCM token for the mobile device
    """
    
    def __init__(self, fcm_server_key: Optional[str] = None):
        self.fcm_server_key = fcm_server_key or os.getenv("FCM_SERVER_KEY")
        self.fcm_endpoint = "https://fcm.googleapis.com/fcm/send"
        
        # Store user's device token (single user)
        self.device_token = None
        self.token_updated_at = None
        
        # Notification queue for offline delivery
        self.notification_queue = []
        
        # Background task for sending queued notifications
        self.is_running = False
    
    def register_device(self, fcm_token: str):
        """Register mobile device token for push notifications"""
        self.device_token = fcm_token
        self.token_updated_at = datetime.now()
        logger.info(f"Device registered with token: {fcm_token[:10]}...")
        
        # Send any queued notifications
        asyncio.create_task(self.send_queued_notifications())
    
    async def send_notification(self, notification: PushNotification) -> bool:
        """
        Send push notification to registered device
        Returns True if sent successfully, False otherwise
        """
        if not self.device_token:
            # Queue for later
            self.notification_queue.append(notification)
            logger.info(f"Notification queued (no device token): {notification.title}")
            return False
        
        try:
            # Prepare FCM payload
            payload = {
                "to": self.device_token,
                "priority": "high",
                "notification": {
                    "title": notification.title,
                    "body": notification.body,
                    "sound": "default",
                    "badge": 1
                },
                "data": {
                    "session_id": notification.session_id,
                    "timestamp": notification.timestamp.isoformat(),
                    **notification.data
                }
            }
            
            # Send via FCM
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"key={self.fcm_server_key}",
                    "Content-Type": "application/json"
                }
                
                async with session.post(self.fcm_endpoint, 
                                       json=payload, 
                                       headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Notification sent: {result}")
                        return True
                    else:
                        logger.error(f"FCM error: {response.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Push notification error: {e}")
            return False
    
    async def send_queued_notifications(self):
        """Send all queued notifications when device registers"""
        if not self.device_token:
            return
        
        while self.notification_queue:
            notification = self.notification_queue.pop(0)
            await self.send_notification(notification)
            await asyncio.sleep(1)  # Rate limit
    
    async def notify_long_task_started(self, session_id: str, task_name: str):
        """Notify user that a long task has started"""
        notification = PushNotification(
            title="Task Started 🔄",
            body=f"Processing: {task_name}",
            data={"type": "task_started", "task": task_name},
            session_id=session_id
        )
        await self.send_notification(notification)
    
    async def notify_task_completed(self, session_id: str, task_name: str, result_summary: str):
        """Notify user that a long task is complete"""
        notification = PushNotification(
            title="Task Complete ✅",
            body=f"{task_name}: {result_summary}",
            data={"type": "task_completed", "task": task_name, "result": result_summary},
            session_id=session_id
        )
        await self.send_notification(notification)
    
    async def notify_approval_needed(self, session_id: str, action: str, details: str):
        """Notify user that human approval is needed"""
        notification = PushNotification(
            title="Approval Needed ⚠️",
            body=f"{action}: {details[:50]}...",
            data={
                "type": "approval_needed",
                "action": action,
                "details": details,
                "approval_id": f"approval_{datetime.now().timestamp()}"
            },
            session_id=session_id
        )
        await self.send_notification(notification)
    
    async def notify_file_ready(self, session_id: str, file_name: str, file_id: str):
        """Notify user that a processed file is ready for download"""
        notification = PushNotification(
            title="File Ready 📁",
            body=f"Your file '{file_name}' is ready to download",
            data={
                "type": "file_ready",
                "file_name": file_name,
                "file_id": file_id
            },
            session_id=session_id
        )
        await self.send_notification(notification)
    
    async def notify_error(self, session_id: str, error_message: str):
        """Notify user of an error"""
        notification = PushNotification(
            title="Error ❌",
            body=error_message[:100],
            data={"type": "error", "error": error_message},
            session_id=session_id
        )
        await self.send_notification(notification)

# Singleton instance
push_service = PushNotificationService()