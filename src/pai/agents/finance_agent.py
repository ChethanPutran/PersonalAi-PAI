from typing import Dict, Any
from loguru import logger
from pai.agents.base_agent import BaseAgent


class FinanceAgent(BaseAgent):
    name = "finance_agent"

    def __init__(self, kernel):
        super().__init__(self.name, kernel)
        self._capabilities = ["finance.track_expense", "finance.get_budget", "finance.investment_advice"]
    
    async def initialize(self) -> None:
        self._capabilities = ["finance.track_expense", "finance.get_budget", "finance.investment_advice"]
    
    async def process_goal(self, goal: str, context: Dict) -> Dict:
        if "expense" in goal.lower() or "track" in goal.lower():
            return await self._track_expense(context)
        elif "budget" in goal.lower():
            return await self._get_budget()
        elif "invest" in goal.lower():
            return await self._investment_advice(context.get('amount', 1000))
        return {"error": "Unknown finance goal"}
    
    async def _track_expense(self, context: Dict) -> Dict:
        amount = context.get('amount')
        category = context.get('category', 'other')
        # Store in memory
        await self.store_memory("long_term", {"type": "expense", "amount": amount, "category": category})
        return {"recorded": True, "amount": amount, "category": category}
    
    async def _get_budget(self) -> Dict:
        # Retrieve spending summary
        return {"total_spent": 1250, "budget_left": 750, "categories": {"food": 300, "transport": 150}}
    
    async def _investment_advice(self, amount: float) -> Dict:
        advice = await self.use_plugin("llm", "complete", {"prompt": f"Investment advice for ${amount}"})
        return {"advice": advice.get('text', 'Consider diversified index funds')}

    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        logger.info(f"FinanceAgent received event {event_type} with data: {data}")
        if event_type == "expense_recorded":
            amount = data.get("amount")
            category = data.get("category")
            logger.info(f"Expense recorded: ${amount} in category {category}")
        elif event_type == "budget_update":
            budget_info = data.get("budget_info", {})
            logger.info(f"Budget updated: {budget_info}")
        elif event_type == "investment_opportunity":
            opportunity = data.get("opportunity", "")
            advice = await self._investment_advice(opportunity)
            logger.info(f"Investment advice for opportunity '{opportunity}': {advice}")