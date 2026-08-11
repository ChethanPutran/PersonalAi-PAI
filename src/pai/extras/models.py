from langchain_classic.schema import BaseMessage, AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import Annotated, List, Dict, Any, Optional, TypedDict
import operator

class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    respond_to_user: bool
    user_input: Optional[str]
    plan: List[str] 
    current_step: int
    memory_context: List[str]
    requires_approval: bool 
    pending_action: Optional[Dict[str, Any]]
    final_answer: Optional[str]
    execution_result: Optional[str]
    summary: str

class ToolCall(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    requires_human_approval: bool

class MemoryEntry(BaseModel):
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: float