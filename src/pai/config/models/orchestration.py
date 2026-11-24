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
    max_concurrent_tasks: int = Field(default=5)
    task_timeout_seconds: int = Field(default=300)  # 5 minutes
    enable_task_retry: bool = Field(default=True)
    retry_delay_seconds: int = Field(default=10)  # 10 seconds
    max_task_retries: int = Field(default=3)
    enable_reflection: bool = Field(default=True)
    

