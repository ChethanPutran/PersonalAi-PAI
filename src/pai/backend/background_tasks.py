import asyncio
from typing import Dict, Any, Callable, Optional
from datetime import datetime
import uuid
import traceback
from .push_notifications import push_service, PushNotification
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BackgroundTaskProcessor:
    """
    Handle long-running tasks with progress tracking and push notifications
    """
    
    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.task_queue = asyncio.Queue()
        self.is_running = False
    
    async def start(self):
        """Start the background task processor"""
        self.is_running = True
        asyncio.create_task(self._process_queue())
    
    async def submit_task(self, 
                         session_id: str, 
                         task_name: str, 
                         task_func: Callable,
                         *args, 
                         **kwargs) -> str:
        """
        Submit a background task
        
        Returns:
            task_id: Unique identifier for the task
        """
        task_id = str(uuid.uuid4())
        
        # Store task info
        self.tasks[task_id] = {
            "id": task_id,
            "session_id": session_id,
            "name": task_name,
            "status": "queued",
            "created_at": datetime.now(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None
        }
        
        # Add to queue
        await self.task_queue.put({
            "task_id": task_id,
            "session_id": session_id,
            "task_name": task_name,
            "task_func": task_func,
            "args": args,
            "kwargs": kwargs
        })
        
        # Send push notification
        await push_service.notify_long_task_started(session_id, task_name)
        
        return task_id
    
    async def _process_queue(self):
        """Process tasks from queue"""
        while self.is_running:
            try:
                task_data = await self.task_queue.get()
                
                # Update task status
                task_id = task_data["task_id"]
                session_id = task_data["session_id"]
                task_name = task_data["task_name"]
                
                self.tasks[task_id]["status"] = "running"
                self.tasks[task_id]["started_at"] = datetime.now()
                
                try:
                    # Execute task
                    result = await task_data["task_func"](
                        *task_data["args"], 
                        **task_data["kwargs"]
                    )
                    
                    # Update success
                    self.tasks[task_id]["status"] = "completed"
                    self.tasks[task_id]["completed_at"] = datetime.now()
                    self.tasks[task_id]["result"] = result
                    
                    # Send completion notification
                    await push_service.notify_task_completed(
                        session_id, 
                        task_name, 
                        str(result)[:100]
                    )
                    
                except Exception as e:
                    # Update error
                    self.tasks[task_id]["status"] = "failed"
                    self.tasks[task_id]["completed_at"] = datetime.now()
                    self.tasks[task_id]["error"] = str(e)
                    
                    # Send error notification
                    await push_service.notify_error(session_id, str(e))
                    
                    logger.error(f"Task {task_id} failed: {traceback.format_exc()}")
                
                finally:
                    self.task_queue.task_done()
                    
            except Exception as e:
                logger.error(f"Task processor error: {e}")
                await asyncio.sleep(1)
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a background task"""
        return self.tasks.get(task_id)
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued task"""
        if task_id in self.tasks and self.tasks[task_id]["status"] == "queued":
            self.tasks[task_id]["status"] = "cancelled"
            return True
        return False

# Singleton
task_processor = BackgroundTaskProcessor()