"""
Executor Manager - Distributed task execution across devices
"""
import logging
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ExecutorType(str, Enum):
    """Types of executors"""
    MOBILE = "mobile"
    DESKTOP = "desktop"
    SERVER = "server"
    EDGE = "edge"


class ExecutorStatus(str, Enum):
    """Executor status"""
    OFFLINE = "offline"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


@dataclass
class ExecutorRegistration:
    """Executor registration info"""
    id: str
    executor_type: ExecutorType
    device_id: str
    device_name: str
    platform: str
    capabilities: List[str]
    status: ExecutorStatus
    resources: Dict[str, Any]  # CPU, memory, etc.
    registered_at: datetime


@dataclass
class TaskSubmission:
    """Task submission to executor"""
    id: str
    executor_id: str
    task_type: str
    priority: int
    payload: Dict[str, Any]
    timeout: int
    submitted_at: datetime
    status: str = "pending"


class ExecutorManager:
    """
    Manages distributed execution across multiple devices.
    
    Responsibilities:
    - Executor registration and discovery
    - Task distribution
    - Device-aware execution routing
    - Resource monitoring
    - Cross-device coordination
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """Initialize executor manager"""
        self.config = config or {}
        self.executors: Dict[str, ExecutorRegistration] = {}
        self.task_queue: List[TaskSubmission] = []
        self.task_results: Dict[str, Dict[str, Any]] = {}
        self.event_bus = None
        logger.info("Executor Manager initialized")
    
    async def initialize(self, event_bus=None):
        """Initialize executor manager"""
        self.event_bus = event_bus
        logger.info("Executor Manager initialized")
    
    async def register_executor(self,
                               executor_type: ExecutorType,
                               device_id: str,
                               device_name: str,
                               platform: str,
                               capabilities: List[str],
                               resources: Dict[str, Any] = None) -> ExecutorRegistration:
        """Register a new executor"""
        executor_id = str(uuid.uuid4())
        
        registration = ExecutorRegistration(
            id=executor_id,
            executor_type=executor_type,
            device_id=device_id,
            device_name=device_name,
            platform=platform,
            capabilities=capabilities,
            status=ExecutorStatus.IDLE,
            resources=resources or {},
            registered_at=datetime.utcnow()
        )
        
        self.executors[executor_id] = registration
        logger.info(f"Executor registered: {device_name} ({executor_type}) - {executor_id}")
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("executor/registered", {
                "executor_id": executor_id,
                "device_name": device_name,
                "type": executor_type.value,
                "capabilities": capabilities
            })
        
        return registration
    
    async def unregister_executor(self, executor_id: str) -> bool:
        """Unregister an executor"""
        if executor_id not in self.executors:
            logger.warning(f"Executor not found: {executor_id}")
            return False
        
        executor = self.executors[executor_id]
        del self.executors[executor_id]
        logger.info(f"Executor unregistered: {executor.device_name}")
        
        if self.event_bus:
            await self.event_bus.publish("executor/unregistered", {
                "executor_id": executor_id,
                "device_name": executor.device_name
            })
        
        return True
    
    async def update_executor_status(self, executor_id: str, status: ExecutorStatus) -> bool:
        """Update executor status"""
        if executor_id not in self.executors:
            logger.warning(f"Executor not found: {executor_id}")
            return False
        
        executor = self.executors[executor_id]
        old_status = executor.status
        executor.status = status
        
        logger.info(f"Executor status updated: {executor_id} - {old_status} -> {status}")
        
        if self.event_bus:
            await self.event_bus.publish("executor/status_changed", {
                "executor_id": executor_id,
                "old_status": old_status.value,
                "new_status": status.value
            })
        
        return True
    
    async def submit_task(self,
                         task_type: str,
                         payload: Dict[str, Any],
                         priority: int = 0,
                         timeout: int = 300,
                         target_executor: Optional[str] = None) -> TaskSubmission:
        """Submit a task for execution"""
        
        # Find best executor if not specified
        if not target_executor:
            target_executor = await self._find_best_executor(task_type, payload)
        
        if not target_executor:
            logger.error(f"No available executor for task type: {task_type}")
            raise Exception("No available executor")
        
        task_id = str(uuid.uuid4())
        task = TaskSubmission(
            id=task_id,
            executor_id=target_executor,
            task_type=task_type,
            priority=priority,
            payload=payload,
            timeout=timeout,
            submitted_at=datetime.utcnow(),
            status="pending"
        )
        
        self.task_queue.append(task)
        logger.info(f"Task submitted: {task_id} to executor {target_executor}")
        
        # Emit event
        if self.event_bus:
            await self.event_bus.publish("executor/task_submitted", {
                "task_id": task_id,
                "executor_id": target_executor,
                "task_type": task_type,
                "priority": priority
            })
        
        return task
    
    async def _find_best_executor(self, task_type: str, payload: Dict[str, Any]) -> Optional[str]:
        """Find best executor for a task"""
        # Find executors with required capability
        available_executors = [
            executor for executor in self.executors.values()
            if task_type in executor.capabilities and executor.status != ExecutorStatus.OFFLINE
        ]
        
        if not available_executors:
            return None
        
        # Sort by status (IDLE first) and return best
        available_executors.sort(
            key=lambda x: (
                x.status == ExecutorStatus.IDLE,  # True (1) comes first
                x.resources.get("available_cpu", 0),
                x.resources.get("available_memory", 0)
            ),
            reverse=True
        )
        
        return available_executors[0].id
    
    async def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task result"""
        return self.task_results.get(task_id)
    
    async def report_task_result(self,
                                 task_id: str,
                                 result: Dict[str, Any]):
        """Report task result from executor"""
        self.task_results[task_id] = result
        
        # Update task status in queue
        for task in self.task_queue:
            if task.id == task_id:
                task.status = result.get("status", "completed")
                break
        
        logger.info(f"Task result reported: {task_id}")
        
        if self.event_bus:
            await self.event_bus.publish("executor/task_completed", {
                "task_id": task_id,
                "status": result.get("status"),
                "result": result
            })
    
    async def get_executor_info(self, executor_id: str) -> Optional[Dict[str, Any]]:
        """Get executor information"""
        if executor_id not in self.executors:
            return None
        
        executor = self.executors[executor_id]
        return {
            "id": executor.id,
            "type": executor.executor_type.value,
            "device_name": executor.device_name,
            "platform": executor.platform,
            "status": executor.status.value,
            "capabilities": executor.capabilities,
            "resources": executor.resources,
            "registered_at": executor.registered_at.isoformat()
        }
    
    async def list_executors(self, status: Optional[ExecutorStatus] = None) -> List[Dict[str, Any]]:
        """List executors"""
        executors_list = []
        
        for executor in self.executors.values():
            if status and executor.status != status:
                continue
            
            executors_list.append({
                "id": executor.id,
                "type": executor.executor_type.value,
                "device_name": executor.device_name,
                "status": executor.status.value,
                "capabilities": executor.capabilities
            })
        
        return executors_list
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status"""
        for task in self.task_queue:
            if task.id == task_id:
                return {
                    "task_id": task_id,
                    "executor_id": task.executor_id,
                    "status": task.status,
                    "task_type": task.task_type,
                    "submitted_at": task.submitted_at.isoformat()
                }
        
        if task_id in self.task_results:
            return self.task_results[task_id]
        
        return None
    
    async def get_manager_status(self) -> Dict[str, Any]:
        """Get executor manager status"""
        online_executors = [e for e in self.executors.values() if e.status != ExecutorStatus.OFFLINE]
        busy_executors = [e for e in online_executors if e.status == ExecutorStatus.BUSY]
        
        return {
            "total_executors": len(self.executors),
            "online_executors": len(online_executors),
            "busy_executors": len(busy_executors),
            "pending_tasks": len([t for t in self.task_queue if t.status == "pending"]),
            "completed_tasks": len(self.task_results),
            "executors": await self.list_executors()
        }
