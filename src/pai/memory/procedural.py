import json
from pathlib import Path
from typing import Dict, Any, List

class ProceduralMemory:
    """Stores and reuses successful workflows."""
    
    def __init__(self, path="./data/procedural.json"):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)
        self.workflows = self._load()
    
    async def initialize(self):
        pass  # No async initialization needed for file-based storage
    
    def _load(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}
    
    def _save(self):
        self.path.write_text(json.dumps(self.workflows, indent=2))
    
    def add_workflow(self, goal_pattern: str, steps: List[Dict], success_count: int = 1):
        if goal_pattern not in self.workflows:
            self.workflows[goal_pattern] = {"steps": steps, "successes": 0, "failures": 0}
        self.workflows[goal_pattern]["successes"] += success_count
        self.workflows[goal_pattern]["steps"] = steps  # overwrite with latest
        self._save()
    
    async def get_workflow(self, goal: str) -> List[Dict]:
        # Find best matching pattern
        best = []
        best_score = 0
        for pattern, data in self.workflows.items():
            if pattern in goal:
                score = data["successes"] / (data["successes"] + data["failures"] + 1)
                if score > best_score:
                    best_score = score
                    best = data["steps"]
        return best
    
    async def shutdown(self):
        self._save()