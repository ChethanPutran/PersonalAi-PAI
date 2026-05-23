import json
import os
import re
from dataclasses import dataclass
from logging import getLogger
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from .enums import NextStates, NodeNames
from .memory import Memory, PercistenceManager
from .models import AgentState
from .tools import SkillManager
from .tools2 import validate_environment

logger = getLogger(__name__)

validate_environment()


@dataclass
class PlannerResult:
    plan: list[str]
    response: str = ""


class AgentGraph:
    def __init__(
        self,
        name: str = "Chvis",
        model: str = "gemini-2.5-flash",
        temperature: float = 1,
        max_tokens: int = 1000,
        visualize: bool = False,
        max_context_window: int = 3000,
        scale_factor: float = 0.5,
        db_path: str = "agent.db",
    ):
        self.skills = SkillManager()
        self.name = name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_context_window = max_context_window
        self.scale_factor = scale_factor
        self.db_path = db_path
        self.percistence_manager = PercistenceManager()
        self.checkpointer = self.percistence_manager.get_checkpointer()
        self.memory = Memory()
        self.config = {"thread_id": "default_thread", "metadata": {"thread_id": "default_thread"}}
        self._pending_states: Dict[str, AgentState] = {}

        self.llm = None
        self.llm_with_tools = None
        self._init_llm(model=model)
        self._build_graph(visualize)

    # -------------------- LLM setup --------------------
    def _init_llm(self, model: str, provider: Optional[str] = None):
        provider = provider or self._select_provider()

        if provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            self.llm = ChatGoogleGenerativeAI(
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_retries=1,
            )
        elif provider == "nvidia":
            from langchain_nvidia_ai_endpoints import ChatNVIDIA

            self.llm = ChatNVIDIA(
                model=model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        if self.llm is not None:
            self.llm_with_tools = self.llm.bind_tools(self.skills.list_tools())

    def _select_provider(self) -> Optional[str]:
        if os.getenv("GOOGLE_API_KEY"):
            return "google"
        if os.getenv("NVIDIA_API_KEY"):
            return "nvidia"
        return None

    def _has_llm(self) -> bool:
        return self.llm is not None

    # -------------------- Fallback heuristics --------------------
    def _build_fallback_plan(self, user_input: str) -> PlannerResult:
        text = user_input.lower()
        plan: list[str] = []

        if any(word in text for word in ["weather", "temperature", "forecast"]):
            plan.append(f"Get weather for {self._extract_location(user_input) or 'the requested location'}")
        if any(word in text for word in ["time", "clock", "timezone"]):
            plan.append("Get the current time")
        if any(word in text for word in ["search", "web", "look up", "find online"]):
            plan.append(f"Search the web for {user_input}")
        if any(word in text for word in ["file", "read", "write", "save", "open"]):
            plan.append("Perform a file operation")
        if any(word in text for word in ["expense", "budget", "spend"]):
            plan.append("Update expense tracking")
        if not plan:
            return PlannerResult(plan=[], response=f"I can help with that: {user_input}")
        return PlannerResult(plan=plan, response="")

    def _extract_location(self, text: str) -> Optional[str]:
        match = re.search(r"(?:in|for)\s+([A-Za-z0-9,\-\s]+)$", text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _fallback_action(self, step: str) -> Dict[str, Any]:
        text = step.lower()
        if "weather" in text:
            return {"skill": "weather_tool", "params": {"location": self._extract_location(step) or "New York"}, "requires_approval": False}
        if "time" in text:
            return {"skill": "time_tool", "params": {"timezone": "local"}, "requires_approval": False}
        if "search" in text:
            return {"skill": "web_search", "params": {"query": step}, "requires_approval": False}
        if "file" in text:
            return {"skill": "file_ops", "params": {"action": "read", "path": step.split()[-1]}, "requires_approval": True}
        if "expense" in text:
            return {"skill": "expense_tracker", "params": {"action": "summary"}, "requires_approval": False}
        return {"skill": "none", "params": {}, "requires_approval": False}

    def _invoke_llm(self, messages):
        if not self._has_llm():
            raise RuntimeError("No LLM provider configured")
        return self.llm_with_tools.invoke(messages)

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        if "```json" in raw:
            raw = raw.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1].split("```", 1)[0]
        try:
            data = json.loads(raw.strip())
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _execute_action(self, action: Dict[str, Any], step: str) -> tuple[str, Any]:
        skill_name = action.get("skill", "none")
        params = action.get("params", {}) or {}

        if skill_name == "none":
            return step, None

        result = self.skills.execute_skill(skill_name, params)
        self.memory.store_skill_result(skill_name, json.dumps(params), json.dumps(result))
        return f"Executed {skill_name}: {result}", result

    # -------------------- Graph --------------------
    def _build_graph(self, visualize: bool = False):
        workflow = StateGraph(AgentState)
        workflow.add_node(NodeNames.PLANNER.value, self.planner_node)
        workflow.add_node(NodeNames.EXECUTOR.value, self.executor_node)
        workflow.add_node(NodeNames.VERIFIER.value, self.verifier_node)
        workflow.add_node(NodeNames.RESPONDER.value, self.responder_node)
        workflow.add_node(NodeNames.CLEANUP.value, self.cleanup_context_window)
        workflow.set_entry_point(NodeNames.PLANNER.value)
        workflow.add_conditional_edges(
            NodeNames.PLANNER.value,
            self.should_execute,
            {
                NextStates.EXECUTE: NodeNames.EXECUTOR.value,
                NextStates.RESPOND: NodeNames.RESPONDER.value,
                NextStates.DONE: NodeNames.CLEANUP.value,
            },
        )
        workflow.add_conditional_edges(
            NodeNames.EXECUTOR.value,
            self.after_execution,
            {
                NextStates.CONTINUE: NodeNames.EXECUTOR.value,
                NextStates.NEED_APPROVAL: NodeNames.CLEANUP.value,
                NextStates.DONE: NodeNames.VERIFIER.value,
            },
        )
        workflow.add_edge(NodeNames.VERIFIER.value, NodeNames.RESPONDER.value)
        workflow.add_edge(NodeNames.RESPONDER.value, NodeNames.CLEANUP.value)
        workflow.add_edge(NodeNames.CLEANUP.value, END)

        if self.checkpointer is not None:
            self.workflow = workflow.compile(checkpointer=self.checkpointer)
        else:
            self.workflow = workflow.compile()

        self.percistence_manager.register_graph(self.workflow)
        if visualize:
            self._visualize_graph()

    def _visualize_graph(self):
        print(self.workflow.get_graph().draw_mermaid())

    def cleanup_context_window(self, state: AgentState):
        messages = state.get("messages", [])
        if len(messages) <= max(1, int(self.max_context_window * self.scale_factor)):
            return {}

        recent = messages[-5:]
        prompt = "Summarize the conversation using the latest context."
        summary_source = state.get("summary", "")

        if self._has_llm():
            response = self.llm.invoke(
                [SystemMessage(content="Summarize user context briefly."), *recent, HumanMessage(content=prompt)]
            )
            summary = str(response.content)
        else:
            summary = summary_source or "Conversation summarized."

        return {
            "summary": summary,
            "messages": recent,
        }

    def call_model(self, prompt: str, state: AgentState):
        if not self._has_llm():
            return SimpleNamespace(content="")

        system_prompt = (
            f"You are {self.name}, an intelligent assistant. "
            f"Follow the instructions carefully and use tools when needed. {self.skills.list_skills()}"
        )
        messages = [SystemMessage(content=system_prompt)]
        if state.get("summary"):
            messages.append(AIMessage(content=f"Summary of previous interactions: {state['summary']}"))
        messages.extend(state.get("messages", []))
        return self._invoke_llm(messages + [HumanMessage(content=prompt)])

    def planner_node(self, state: AgentState):
        user_input = state.get("user_input") or ""

        if self._has_llm():
            prompt = f"""
Analyze the user request and return valid JSON only.

Available skills: {self.skills.list_skills()}

If tools are needed, return:
{{"plan": ["step 1", "step 2"], "response": ""}}

If no tools are needed, return:
{{"plan": [], "response": "final answer"}}

User request:
{user_input}
"""
            response = self.call_model(prompt, state)
            parsed = self._parse_json(str(response.content)) or {}
            plan = parsed.get("plan") if isinstance(parsed.get("plan"), list) else []
            response_text = str(parsed.get("response", ""))
        else:
            fallback = self._build_fallback_plan(user_input)
            plan = fallback.plan
            response_text = fallback.response

        return {
            "plan": plan,
            "current_step": 0,
            "respond_to_user": len(plan) == 0,
            "final_answer": response_text if not plan else None,
            "execution_result": state.get("execution_result"),
        }

    def should_execute(self, state: AgentState) -> Literal[NextStates.EXECUTE, NextStates.RESPOND, NextStates.DONE]:
        plan = state.get("plan", [])
        if plan:
            return NextStates.EXECUTE
        if state.get("respond_to_user"):
            return NextStates.RESPOND
        return NextStates.DONE

    def executor_node(self, state: AgentState):
        current_step_index = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current_step_index >= len(plan):
            return {"current_step": current_step_index, "requires_approval": False, "pending_action": None}

        step = plan[current_step_index]
        if self._has_llm():
            prompt = f"""
Choose the best skill for this step and return valid JSON only.

Step: {step}
Available skills: {self.skills.list_skills()}

Format:
{{"skill": "skill_name", "params": {{}}, "requires_approval": false}}
"""
            response = self.call_model(prompt, state)
            action = self._parse_json(str(response.content)) or self._fallback_action(step)
        else:
            action = self._fallback_action(step)

        if action.get("requires_approval"):
            return {
                "requires_approval": True,
                "pending_action": action,
                "current_step": current_step_index,
            }

        execution_text, result = self._execute_action(action, step)
        return {
            "messages": state.get("messages", []) + [AIMessage(content=execution_text)],
            "current_step": current_step_index + 1,
            "requires_approval": False,
            "pending_action": None,
            "execution_result": json.dumps(result) if result is not None else execution_text,
        }

    def after_execution(self, state: AgentState) -> Literal[NextStates.CONTINUE, NextStates.NEED_APPROVAL, NextStates.DONE]:
        if state.get("requires_approval", False):
            return NextStates.NEED_APPROVAL

        current_step = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current_step < len(plan):
            return NextStates.CONTINUE
        return NextStates.DONE

    def verifier_node(self, state: AgentState):
        messages = state.get("messages", [])
        if self._has_llm():
            prompt = f"""
Based on the latest execution history, summarize what happened in 2-3 sentences.
History:
{messages[-5:]}
"""
            verification = self.call_model(prompt, state)
            content = str(verification.content)
        else:
            content = "Execution completed."
        return {"messages": state.get("messages", []) + [AIMessage(content=f"Verification: {content}")]}

    def responder_node(self, state: AgentState):
        execution_result = state.get("execution_result")
        user_input = state.get("user_input") or ""

        if self._has_llm():
            prompt = f"""
Generate a helpful response to the user.

User request: {user_input}
Execution result: {execution_result}
"""
            response = self.call_model(prompt, state)
            response_content = str(response.content)
        elif state.get("final_answer"):
            response_content = state["final_answer"]
        elif execution_result:
            response_content = str(execution_result)
        else:
            response_content = "Done."

        if user_input:
            self.memory.add_conversation(user_input, response_content)

        return {
            "final_answer": response_content,
            "messages": state.get("messages", []) + [AIMessage(content=response_content)],
        }

    async def resume_with_approval(self, thread_id: str, approved: bool):
        pending = self._pending_states.get(thread_id)
        if not pending:
            return {"final_answer": "No pending action to resume."}

        if not approved:
            pending["requires_approval"] = False
            pending["pending_action"] = None
            pending["current_step"] = pending.get("current_step", 0) + 1
            self._pending_states.pop(thread_id, None)
            return self._finish_from_state(pending, thread_id)

        pending["requires_approval"] = False
        action = pending.get("pending_action") or {}
        plan = pending.get("plan", [])
        step_index = pending.get("current_step", 0)
        step = plan[step_index] if step_index < len(plan) else "approval"
        execution_text, result = self._execute_action(action, step)
        pending["execution_result"] = json.dumps(result) if result is not None else execution_text
        pending["current_step"] = pending.get("current_step", 0) + 1
        pending["pending_action"] = None
        self._pending_states.pop(thread_id, None)
        return self._finish_from_state(pending, thread_id)

    def _finish_from_state(self, state: AgentState, thread_id: str):
        state.update(self.verifier_node(state))
        state.update(self.responder_node(state))
        return {"thread_id": thread_id, "final_answer": state.get("final_answer", "")}

    def run(self, user_input: Optional[str], thread_id: str = "default"):
        state: AgentState = {
            "messages": [],
            "respond_to_user": False,
            "user_input": user_input,
            "plan": [],
            "current_step": 0,
            "memory_context": [],
            "requires_approval": False,
            "pending_action": None,
            "final_answer": None,
            "execution_result": None,
            "summary": "",
        }

        if user_input:
            state["messages"] = [HumanMessage(content=user_input)]

        state.update(self.planner_node(state))
        if not state.get("plan"):
            state.update(self.responder_node(state))
            return state.get("final_answer", "No response generated.")

        while state.get("current_step", 0) < len(state["plan"]):
            state.update(self.executor_node(state))

            if state.get("requires_approval"):
                self._pending_states[thread_id] = state.copy()
                print(f"\n🛑 PAUSED: Action requires approval: {state.get('pending_action', {})}")
                user_choice = input("Type 'yes' to proceed or 'no' to cancel: ").strip().lower()
                if user_choice == "yes":
                    pending_action = state.get("pending_action") or {}
                    execution_text, result = self._execute_action(
                        pending_action,
                        state.get("plan", ["approval"])[state.get("current_step", 0)],
                    )
                    state["execution_result"] = json.dumps(result) if result is not None else execution_text
                    state["messages"] = state.get("messages", []) + [AIMessage(content=execution_text)]
                state["requires_approval"] = False
                state["pending_action"] = None
                state["current_step"] = state.get("current_step", 0) + 1
                continue

        state.update(self.verifier_node(state))
        state.update(self.responder_node(state))
        return state.get("final_answer", "No response generated.")
