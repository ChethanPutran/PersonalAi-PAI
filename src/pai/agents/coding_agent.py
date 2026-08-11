from typing import Dict, Any
from loguru import logger
from pai.agents.base_agent import BaseAgent

class CodingAgent(BaseAgent):
    name = "coding_agent"
    
    async def initialize(self) -> None:
        self._capabilities = ["coding.analyze", "coding.debug", "coding.explain"]
    
    async def process_goal(self, goal: str, context: Dict) -> Dict:
        code = context.get('code', '')
        if "debug" in goal.lower():
            return await self._debug(code)
        elif "explain" in goal.lower():
            return await self._explain(code)
        elif "improve" in goal.lower():
            return await self._improve(code)
        return {"error": "Unknown coding goal"}
    
    async def _debug(self, code: str) -> Dict:
        # Use LLM to find errors
        suggestion = await self.use_plugin("llm", "complete", {"prompt": f"Find bugs in this code:\n{code}"})
        return {"suggestions": suggestion.get('text', 'No obvious errors')}
    
    async def _explain(self, code: str) -> Dict:
        explanation = await self.use_plugin("llm", "complete", {"prompt": f"Explain this code:\n{code}"})
        return {"explanation": explanation.get('text')}
    
    async def _improve(self, code: str) -> Dict:
        improved = await self.use_plugin("llm", "complete", {"prompt": f"Improve this code:\n{code}"})
        return {"improved_code": improved.get('text', code)}