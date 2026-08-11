from typing import Dict, Any, List, Optional
import asyncio
from loguru import logger
from pai.agents.base_agent import BaseAgent
from pai.agents.research_agent import ResearchAgent
from pai.agents.productivity_agent import ProductivityAgent
from pai.agents.communication_agent import CommunicationAgent
from pai.agents.travel_agent import TravelAgent
from pai.agents.health_agent import HealthAgent
from pai.agents.automation_agent import AutomationAgent
from pai.agents.coding_agent import CodingAgent
from pai.agents.finance_agent import FinanceAgent

class AgentManager:
    """Manages lifecycle, coordination, and goal routing for all agents."""
    
    def __init__(self, kernel):
        self.kernel = kernel
        self._agents: Dict[str, BaseAgent] = {}
        self._agent_capabilities: Dict[str, str] = {}  # capability -> agent_name
    
    async def initialize(self) -> None:
        # Instantiate all agents
        agent_classes = [
            ResearchAgent, ProductivityAgent, CommunicationAgent,
            TravelAgent, HealthAgent, AutomationAgent, CodingAgent, FinanceAgent
        ]
        for cls in agent_classes:
            agent = cls(self.kernel)
            await agent.initialize()
            self._agents[agent.name] = agent
            for cap in agent.get_capabilities():
                self._agent_capabilities[cap] = agent.name
        logger.info(f"AgentManager initialized with {len(self._agents)} agents")
    
    async def start(self) -> None:
        for agent in self._agents.values():
            await agent.start()
        logger.info("All agents started")
    
    async def stop(self) -> None:
        for agent in self._agents.values():
            await agent.stop()
    
    async def send_goal(self, agent_name: str, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' not found")
        return await agent.process_goal(goal, context)
    
    async def route_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Automatically select the best agent for a goal."""
        goal_lower = goal.lower()
        if any(word in goal_lower for word in ["travel", "flight", "hotel", "taxi", "transport"]):
            agent_name = "travel_agent"
        elif any(word in goal_lower for word in ["health", "exercise", "workout", "fitness", "steps"]):
            agent_name = "health_agent"
        elif any(word in goal_lower for word in ["code", "debug", "program", "function"]):
            agent_name = "coding_agent"
        elif any(word in goal_lower for word in ["finance", "budget", "expense", "money"]):
            agent_name = "finance_agent"
        elif any(word in goal_lower for word in ["automate", "workflow", "script"]):
            agent_name = "automation_agent"
        elif any(word in goal_lower for word in ["schedule", "todo", "remind", "task"]):
            agent_name = "productivity_agent"
        elif any(word in goal_lower for word in ["translate", "summarize", "conversation"]):
            agent_name = "communication_agent"
        else:
            agent_name = "research_agent"  # default
        return await self.send_goal(agent_name, goal, context)
    
    async def handle_agent_event(self, event_type: str, data: Dict[str, Any]) -> None:
        for agent in self._agents.values():
            await agent.handle_event(event_type, data)
    
    async def get_agent_status(self, agent_name: Optional[str] = None) -> Dict[str, Any]:
        if agent_name:
            agent = self._agents.get(agent_name)
            return {agent_name: {"running": agent._running if agent else False}}
        return {name: {"running": agent._running} for name, agent in self._agents.items()}