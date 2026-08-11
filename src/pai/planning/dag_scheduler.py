from typing import List, Dict
import asyncio

class DAGScheduler:

    def __init__(self, workflow_executor, tasks: List[Dict], dependencies: Dict[str, List[str]]):
        self.workflow_executor = workflow_executor
        self.tasks = tasks
        self.dependencies = dependencies
        self.task_map = {t["id"]: t for t in tasks}
        self.in_degree = {tid: len(dependencies.get(tid, [])) for tid in self.task_map}
        self.ready = [tid for tid, deg in self.in_degree.items() if deg == 0]  
        
    async def execute_dag(self, tasks: List[Dict], dependencies: Dict[str, List[str]]):
        """Execute tasks respecting dependencies (DAG)."""
        task_map = {t["id"]: t for t in tasks}
        in_degree = {tid: len(dependencies.get(tid, [])) for tid in task_map}
        ready = [tid for tid, deg in in_degree.items() if deg == 0]
        results = {}
        
        async def run_task(tid):
            result = await self.workflow_executor._do_task(task_map[tid])
            results[tid] = result
            for dependent in [d for d, deps in dependencies.items() if tid in deps]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    ready.append(dependent)
        
        while ready:
            await asyncio.gather(*[run_task(tid) for tid in ready])
            ready = []
        
        return results