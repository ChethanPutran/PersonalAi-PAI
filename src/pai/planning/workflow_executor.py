import asyncio
from typing import Dict, Any, List, Optional
from loguru import logger

class TaskFailedError(Exception):
    def __init__(self, task_id, original_error):
        self.failed_task = task_id
        super().__init__(f"Task {task_id} failed: {original_error}")

class WorkflowExecutor:
    """Executes sequences of tasks with retries and state tracking."""
    
    def __init__(self,kernel):
        self._progress: Dict[str, Dict[str, Any]] = {}
        self.kernel = kernel
    
    async def initialize(self) -> None:
        logger.info("WorkflowExecutor initialized")
    
    async def execute(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for task in tasks:
            result = await self._execute_task(task)
            results.append(result)
            if result.get("status") == "failed":
                # Optionally break, but we continue by default
                pass
        return results
    
    async def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task["id"]
        self._progress[task_id] = {"status": "running", "attempts": 0}
        task_id = task["id"]
        strategies = [
            self._execute_with_same_params,
            self._execute_with_alternate_params,
            self._execute_with_fallback_plugin
        ]
        for strategy in strategies:
            for attempt in range(2):
                try:
                    result = await strategy(task)
                    self._progress[task_id] = {"status": "completed", "result": result}
                    return {"task_id": task_id, "status": "completed", "result": result}
                except Exception as e:
                    logger.warning(f"Strategy {strategy.__name__} attempt {attempt+1} failed: {e}")
                    continue
            # If strategy fails, move to next
        raise TaskFailedError(task_id, "All strategies exhausted")

    async def _execute_with_same_params(self, task: Dict) -> Any:
        return await self._do_task(task)
    
    async def _execute_with_alternate_params(self, task: Dict) -> Any:
        # Modify task params (e.g., use different selector, different URL)
        alt_task = task.copy()
        if "selector" in alt_task:
            alt_task["selector"] = alt_task["selector"] + "_alt"
        return await self._do_task(alt_task)

    async def _execute_with_fallback_plugin(self, task: Dict) -> Any:
        # Try a different plugin for same capability
        capability = task.get("capability")
        alt_plugin = await self.kernel.capability_router.get_alternate_plugin(capability)
        return await alt_plugin.execute(task["action"], task["params"])

    async def _do_task(self, task: Dict[str, Any]) -> Any:
        # Placeholder – in production, dispatch to capability router
        task_type = task.get("type")
        if task_type == "search":
            return f"Searched for {task.get('query')}"
        elif task_type == "browser_navigate":
            return f"Navigated to {task.get('url')}"
        elif task_type == "calendar_event":
            return "Calendar event created"
        elif task_type == "notification":
            return "Notification sent"
        else:
            return f"Executed {task_type}: {task}"
    
    def get_progress(self, plan_id: str) -> Dict[str, Any]:
        # Simplified; would aggregate by plan_id in real impl
        return self._progress
    
    async def cancel(self, plan_id: str) -> None:
        self._progress = {k: v for k, v in self._progress.items() if not k.startswith(plan_id)}
    
    async def shutdown(self) -> None:
        pass