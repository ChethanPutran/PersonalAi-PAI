from pydantic import BaseModel, Field


class OrchestratorConfig(BaseModel):
    """Orchestrator configuration."""
    goal_tokens_trace_size: int = Field(default=200)
    memory_recall_limit: int = Field(default=5)
    memory_block_max_size: int = Field(default=800)
    tool_summary_block_max_size: int = Field(default=800)
    prompt_trace_max_size: int = Field(default=800)
    chat_reply_max_tokens: int = Field(default=800)
    chat_reply_temperature: float = Field(default=0.7)
    # Skip the planner LLM call for plain conversation (rules, then a tiny classifier call on the fast chat
    # model); anything that might need a tool still goes through the planner. See orchestration/fast_route.py.
    fast_chat_path: bool = Field(default=True)
    # Show the planner only the capabilities that fit the request (planning/capability_select.py): a shorter
    # prompt and a faster planning call. Any doubt, or a plan naming a capability that was not shown, re-plans
    # with the full list. Off by default until it has been compared against real traffic.
    capability_retrieval: bool = Field(default=False)
    capability_retrieval_families: int = Field(default=4, ge=1, le=12)
    fast_route_timeout_seconds: float = Field(default=2.5, gt=0.2)
    max_concurrent_tasks: int = Field(default=5)
    task_timeout_seconds: int = Field(default=300)  # 5 minutes
    enable_task_retry: bool = Field(default=True)
    retry_delay_seconds: int = Field(default=10)  # 10 seconds
    max_task_retries: int = Field(default=3)
    enable_reflection: bool = Field(default=True)
    

