import re

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from typing import Literal, Optional
import json
from langchain_core.runnables import RunnableConfig
from logging import getLogger
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately,trim_messages
from src.models import AgentState
from src.memory import Memory, PercistenceManager
from src.tools import SkillManager
from .enums import *
from .tools2 import validate_environment

logger = getLogger(__name__)


validate_environment()



class AgentGraph:
    def __init__(self,
                 name: str = "Chvis",
                 model="gemini-2.5-flash",
                 temperature: float = 1,
                 max_tokens: int = 1000,
                 visualize: bool = False,
                 max_context_window: int = 3000,
                 scale_factor: float = 0.5,
                 db_path: str = "agent.db"):
        self.skills = SkillManager()
        self.name = name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_context_window = max_context_window
        self.scale_factor = scale_factor
        self.db_path = db_path
        self.percistence_manager = PercistenceManager()
        self.checkpointer = self.percistence_manager.get_checkpointer()
        self.memory = Memory() # In-memory memory manager that interfaces with the SqliteSaver for persistence
        self.config = {
            "thread_id": "default_thread",
            "metadata": {
                    "thread_id": "default_thread",
                }
                }
        self._init_llm(model=model, provider="google")
        self._build_graph(visualize)

    # ==================== LLM ====================
    def _init_llm(self, model: str, provider: Optional[str] = "google"):
        if provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            # Initialize Google Gemini Pro model
            self.llm = ChatGoogleGenerativeAI(
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_retries=1
            )
        else:
            self.llm = ChatNVIDIA(
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

        self.llm_with_tools = self.llm.bind_tools(self.skills.list_tools())
    
    def cleanup_context_window(self, state: AgentState):
        """Helper function to trim old messages from the state to manage token limits"""
        messages = state.get("messages", [])

        # Determine how many recent messages to keep based on the scale factor and max context window
        use_last_k_messages = min(5, self.max_context_window*self.scale_factor/2)  


        if len(messages) > self.max_context_window*self.scale_factor:
            existing_summary = state['summary']
            if existing_summary:
                prompt = (
                    f"Existing summary:\n{existing_summary}\n\n"
                    "Extend the summary using the new conversation above"
                )
            else:
                prompt = (
                    "Summarize the conversationabove:\n"
                )

            response = self.llm.invoke(state["messages"] + [HumanMessage(content=prompt)] )
            to_remove = messages[:-use_last_k_messages]  # Keep last 5 messages

            # Create RemoveMessage instances for messages to be removed
            messages = [RemoveMessage(id=m.id) for m in to_remove]

            # Trim messages to fit within token limits, keeping the most recent ones
            # messages = trim_messages(
            #     state['messages'],
            #     strategy="last",
            #     token_counter=count_tokens_approximately,
            #     max_tokens=self.max_context_window - self.max_tokens
            # )

            return {
                "summary": response.content,
                "messages": messages

            }

        return {}

    def call_model(self, prompt: str, state: AgentState):
        """Helper function to call the LLM with proper message formatting and token management"""
        messages = state.get("messages", [])
        # Always include system prompt with agent instructions
        system_prompt = f"You are {self.name}, an intelligent assistant. \
                         Follow the instructions carefully and use tools when needed. {self.skills.list_skills()}"
        
        system_message = [SystemMessage(content=system_prompt)] 

        if "summary" in state:
            messages = system_message + [AIMessage(content=f"Summary of previous interactions: {state['summary']}")] + messages
        else:
            messages = system_message + [AIMessage(content=f"Summary of previous interactions: {state['messages']}")]

        try:
            response = self.llm_with_tools.invoke(messages + [HumanMessage(content=prompt)])
            return response
        except Exception as e:
            logger.error(f"LLM call error: {e}")
            raise e

    def _build_graph(self, visualize: bool = False):
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node(NodeNames.PLANNER.value, self.planner_node)
        workflow.add_node(NodeNames.EXECUTOR.value, self.executor_node)
        workflow.add_node(NodeNames.VERIFIER.value, self.verifier_node)
        workflow.add_node(NodeNames.RESPONDER.value, self.responder_node)
        workflow.add_node(NodeNames.CLEANUP.value, self.cleanup_context_window)

        # Set entry point
        workflow.set_entry_point(NodeNames.PLANNER.value)

        # Add conditional edges
        workflow.add_conditional_edges(
            NodeNames.PLANNER.value,
            self.should_execute,
            {
                NextStates.EXECUTE: NodeNames.EXECUTOR.value,
                NextStates.RESPOND: NodeNames.RESPONDER.value,
                NextStates.DONE: NodeNames.CLEANUP.value
            }
        )

        workflow.add_conditional_edges(
            NodeNames.EXECUTOR.value,
            self.after_execution,
            {
                NextStates.CONTINUE: NodeNames.VERIFIER.value,
                NextStates.NEED_APPROVAL: NodeNames.EXECUTOR.value,  # Wait for human input
                NextStates.DONE: NodeNames.CLEANUP.value
            }
        )

        workflow.add_edge(NodeNames.VERIFIER.value, NodeNames.RESPONDER.value)
        workflow.add_edge(NodeNames.RESPONDER.value, NodeNames.CLEANUP.value)
        workflow.add_edge(NodeNames.RESPONDER.value, NodeNames.CLEANUP.value)
        workflow.add_edge(NodeNames.CLEANUP.value, END)

        self.workflow = workflow.compile(checkpointer=self.checkpointer)

        # Register the graph with the persistence manager for checkpointing
        self.percistence_manager.register_graph(self.workflow)

        if visualize:
            self._visualize_graph()

    # ==================== GRAPH VIS ====================

    def _visualize_graph(self):
        print(self.workflow.get_graph().draw_mermaid())

    def planner_node(self, state: AgentState):
        """Break down user request into steps"""
        # Get user input from state
        user_input = state.get("user_input")

        # Get messages from state
        # previous_messages = state.get("messages", [])


        # Retrieve similar past conversations
        # similar_memories = self.memory.retrieve_similar(last_message, k=3)
        # memory_context = "\n".join(similar_memories) if similar_memories else ""

        prompt = f"""
        ### Role
        You are a strategic planning engine for an AI assistant. Your goal is to determine if a user request requires external tools (skills) or if it can be answered directly.

        ### Available Skills
        {self.skills.list_skills()}

        ### Task
        Analyze the user request and provide a response in the specified JSON format.

        ### Constraints
        1. If tools are required: 
        - Provide a step-by-step "plan" as a list of strings.
        - Set "response" to an empty string.
        2. If NO tools are required:
        - Provide an empty list for the "plan".
        - Provide the final answer in the "response" field.
        3. Output ONLY valid JSON. Do not include markdown formatting like ```json.

        ### User Request
        "{user_input}"

        ### Output Format
        {{
            "plan": ["Step 1...", "Step 2..."],
            "response": "The direct answer to the user (if no tools needed)"
        }}
        """

        def extract_json(text):
            match = re.search(r'\{.*\}|\[.*\]', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return None

        response = self.call_model(prompt, state)
        response_content = str(response.content)
        plan = []
        res_text = ""
        try:
            # Extract JSON from response
            if "```json" in response_content:
                response_content = response_content.split("```json")[1].split("```")[0]
            elif "```" in response_content:
                response_content = response_content.split("```")[1].split("```")[0]

            res = json.loads(response_content.strip())

            if isinstance(res, dict) and "plan" in res:
                if isinstance(res["plan"], list):
                    plan = res["plan"]
                    res_text = res.get("response", "")
        except json.JSONDecodeError as e:
            logger.error(f"Planner error: {e}")

        return {
            "plan": len(plan) if plan else [],
            "current_step": 0,
            # "memory_context": similar_memories,
            "respond_to_user": len(plan) == 0,  # If no plan, we can respond directly
            "final_answer": res_text if len(plan) == 0 else None  # If no plan, use response as final answer
        }

    def should_execute(self, state: AgentState) -> Literal[NextStates.EXECUTE, NextStates.RESPOND, NextStates.DONE]:
        """Decide if we need to execute tools or just respond"""
        plan = state.get("plan", [])
        if plan and len(plan) > 0:
            return NextStates.EXECUTE
        if state.get("respond_to_user"):
            return NextStates.DONE
        return NextStates.RESPOND

    def executor_node(self, state: AgentState):
        """Execute current step in the plan"""
        current_step_index = state.get("current_step", 0)
        plan = state.get("plan", [])

        if current_step_index >= len(plan):
            return {"current_step": current_step_index}


        step = plan[current_step_index]

        # Use LLM to determine which skill to call
        prompt = f"""
        Execute this step: {step}
        
        Available skills: {self.skills.list_skills()}
        
        Return ONLY valid JSON in this format: 
        {{"skill": "skill_name", "params": {{}}, "requires_approval": true/false}}

        If no skill matches, return: {{"skill": "none", "params": {{}}, "requires_approval": false}}
        """

        try:
            response = self.call_model(prompt, state)
            response_content = str(response.content)

            # Extract JSON
            if "```json" in response_content:
                response_content = response_content.split("```json")[1].split("```")[0]
            elif "```" in response_content:
                response_content = response_content.split("```")[1].split("```")[0]
            
            action = json.loads(response_content.strip())
        except Exception as e:
            logger.error(f"Executor parse error: {e}")
            action = {"skill": "none", "params": {}, "requires_approval": False}


        # Check if human approval needed
        if action.get("requires_approval", False):
            return {
                "requires_approval": True,
                "pending_action": action
            }

        # Execute skill if valid
        result = None
        if action.get("skill") != "none":
            result = self.skills.execute_skill(action["skill"], action.get("params", {}))
            
            # Store in memory
            self.memory.store_skill_result(
                action["skill"],
                json.dumps(action.get("params", {})),
                json.dumps(result)
            )

        return {
            "messages": [AIMessage(content=f"Executed {action.get('skill', 'none')}: {result}" if result else f"Step: {step}")] + state.get("messages", []),
            "current_step": current_step_index + 1,
            "requires_approval": False,
            "pending_action": None
        }

    def after_execution(self, state: AgentState) -> Literal[NextStates.CONTINUE, NextStates.NEED_APPROVAL, NextStates.DONE]:
        """Determine next step after execution"""
        if state.get("requires_approval", False):
            return NextStates.NEED_APPROVAL

        current_step = state.get("current_step", 0)
        plan = state.get("plan", [])
        
        if current_step >= len(plan):
            return NextStates.DONE

        return NextStates.CONTINUE
    
    def verifier_node(self, state: AgentState):
        """Verify execution results"""
        messages = state.get("messages", [])
        
        prompt = f"""
        Based on the execution history:
        {messages[-5:]}  # Last 5 messages for context
        
        Summarize what was accomplished and if any issues occurred.
        Keep response brief (2-3 sentences).
        """

        try:
            verification = self.call_model(prompt, state)
            verification_content = str(verification.content)
        except Exception as e:
            logger.error(f"Verifier error: {e}")
            verification_content = "Execution completed"

        return {
            "messages": [AIMessage(content=f"Verification: {verification_content}")]
        }
    
    def responder_node(self, state: AgentState):
        """Generate final response to user"""
        execution_result = state.get("execution_result", None)
        
        # # Retrieve relevant memories for response
        # last_user_msg = next((m.content for m in reversed(messages) if m.type == "human"),
        #     ""
        # )
        
        # similar = self.memory.retrieve_similar(last_user_msg, k=2) if last_user_msg else []

        prompt = f"""
        Generate a helpful response to the user based on the following execution results and relevant past conversations:
        Execution results: {execution_result}
        """

        try:
            response = self.call_model(prompt, state)
            response_content = str(response.content)
        except Exception as e:
            logger.error(f"Responder error: {e}")
            response_content = "I'm having trouble processing that. Please try again."

        # Store in memory
        if execution_result:
            self.memory.add_conversation(state.get("user_input", ""), response_content)

        return {
            "final_answer": response_content,
            "messages": [AIMessage(content=response_content)]
        }

    async def resume_with_approval(self, thread_id: str, approved: bool):
        # Resume interrupted execution
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if approved:
            # Clear approval flag to proceed
            state: AgentState = {"requires_approval": False}  # type: ignore

            return await self.workflow.ainvoke(state, config)
        else:
            # Skip current step and mark as cancelled
            current_state = self.workflow.get_state(config)
            if current_state and current_state.values:
                current_step = current_state.values.get("current_step", 0)
                state: AgentState = {
                    "requires_approval": False,
                    "current_step": current_step + 1
                } # type: ignore
        return await self.workflow.ainvoke(None, config)
       

    def run(self, user_input: Optional[str], thread_id: str = "default"):
        """Main entry point for running the agent"""
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        # 1. Prepare input
        # If we have user_input, it's a new starting point. 
        # If user_input is None, we are resuming an interrupted graph.
        input_data = None
        if user_input:
            input_data = {"user_input": user_input}

        try:
            res = self.workflow.invoke(input_data, config)

            # Check if we just hit an interrupt
            if res.get("requires_approval"):
                print(f"\n🛑 PAUSED: Action requires approval: {res.get('pending_action', {})}")
                user_choice = input("Type 'yes' to proceed or 'no' to cancel: ").lower()
                
                # Resume by calling run again without input
                if user_choice == 'yes':
                    # Clear the flag and resume
                    self.workflow.update_state(config, {"requires_approval": False})
                else:
                    # Increment step to skip the current planned action
                    curr_step = res.get("current_step", 0)
                    self.workflow.update_state(config, {
                        "requires_approval": False, 
                        "current_step": curr_step + 1
                    })
            return res.get("final_answer", "No response generated.")
        except Exception as e:
            raise e
        
