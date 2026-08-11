import pytest
from pai.planning.planning_engine import PlanningEngine
from pai.planning.goal_decomposer import GoalDecomposer
from pai.planning.workflow_executor import WorkflowExecutor

@pytest.mark.asyncio
async def test_goal_decomposer():
    gd = GoalDecomposer()
    await gd.initialize()
    tasks = await gd.decompose("Search for Python tutorials", {})
    assert len(tasks) > 0
    assert tasks[0]["type"] == "search"

@pytest.mark.asyncio
async def test_workflow_executor():
    we = WorkflowExecutor()
    await we.initialize()
    tasks = [{"id": "t1", "type": "search", "query": "test"}]
    results = await we.execute(tasks)
    assert results[0]["status"] == "completed"
    progress = we.get_progress("plan123")
    assert "t1" in progress

@pytest.mark.asyncio
async def test_planning_engine():
    pe = PlanningEngine()
    await pe.initialize()
    plan = await pe.create_plan("Test goal", {"test": True})
    assert "id" in plan
    assert plan["status"] == "created"
    # execute
    result = await pe.execute_plan(plan)
    assert result["status"] == "completed"
    await pe.shutdown()