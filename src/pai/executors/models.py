from enum import Enum
from typing import Optional

from pydantic import BaseModel

class ExecutionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class ExecutionResult(BaseModel):
    """
    Represents the result of executing a task.
    """
    task_id: str
    """
    The unique identifier of the executed task.
    """

    status: ExecutionStatus
    """
    The status of the execution (e.g., "pending", "running", "completed", "failed").
    """

    message: Optional[str] = None
    """
    An optional message providing additional information about the execution result.
    """

    error: Optional[str] = None
    """
    An optional error message if the execution failed.
    """

    result: Optional[dict] = None
    """
    An optional dictionary containing the result of the execution, if applicable.
    """

class ScheduleStatus(Enum):
    SCHEDULED = "scheduled"
    FAILED = "failed"

class ScheduleResult(BaseModel):
    """
    Represents the result of scheduling a task.
    """

    task_id: str
    """
    The unique identifier of the scheduled task.
    """

    status: ScheduleStatus
    """
    The status of the scheduling operation (e.g., "scheduled", "failed").
    """

    message: Optional[str] = None
    """
    An optional message providing additional information about the scheduling result.
    """

    error: Optional[str] = None
    """
    An optional error message if the scheduling operation failed.
    """

    executor_id: Optional[str] = None
    """
    The identifier of the executor to which the task was assigned, if applicable.
    """