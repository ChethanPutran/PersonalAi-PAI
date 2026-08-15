import json
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime
from pai.plugins.base_plugin import BasePlugin
from loguru import logger

class TaskManagerPlugin(BasePlugin):
    """Local task and todo management."""
    name = "task_manager"
    TASKS_FILE = Path("./data/tasks.json")

    async def start(self) -> None:
        self._running = True
        # Implement any startup logic here

    async def shutdown(self) -> None:
        pass
    
    async def initialize(self) -> None:
        self.TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not self.TASKS_FILE.exists():
            self.TASKS_FILE.write_text(json.dumps([]))
    
    def get_capabilities(self) -> List[str]:
        return ["task_manager.add_task", "task_manager.list_tasks", "task_manager.complete_task", "task_manager.delete_task"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        tasks = json.loads(self.TASKS_FILE.read_text())
        if action == "task_manager.add_task":
            task = {"id": len(tasks)+1, "title": params['title'], "status": "pending", "created": datetime.utcnow().isoformat()}
            tasks.append(task)
            self.TASKS_FILE.write_text(json.dumps(tasks, indent=2))
            return task
        elif action == "task_manager.list_tasks":
            return [t for t in tasks if params.get('status', 'all') == 'all' or t['status'] == params['status']]
        elif action == "task_manager.complete_task":
            for t in tasks:
                if t['id'] == params['task_id']:
                    t['status'] = 'completed'
                    self.TASKS_FILE.write_text(json.dumps(tasks, indent=2))
                    return t
            return {'error': 'Task not found'}
        elif action == "task_manager.delete_task":
            tasks = [t for t in tasks if t['id'] != params['task_id']]
            self.TASKS_FILE.write_text(json.dumps(tasks, indent=2))
            return {'deleted': params['task_id']}
        raise ValueError(f"Unknown action: {action}")

    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own permission logic here.
        return True


    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        # Implement your event handling logic here
        logger.info(f"Task Manager plugin received event: {event} with data: {data}")