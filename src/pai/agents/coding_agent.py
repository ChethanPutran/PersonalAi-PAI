from typing import Dict, Any
from loguru import logger
from pai.agents.base_agent import BaseAgent

class CodingAgent(BaseAgent):
    name = "coding_agent"

    def __init__(self, kernel):
        super().__init__(self.name, kernel)
        self._capabilities = ["coding.analyze", "coding.debug", "coding.explain", "coding.improve"]
        
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

    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        logger.info(f"CodingAgent received event {event_type} with data: {data}")
        if(event_type == "code_review"):
            code = data.get("code", "")
            review = await self._debug(code)
            logger.info(f"Code review result: {review}")
        elif(event_type == "code_explain"):
            code = data.get("code", "")
            explanation = await self._explain(code)
            logger.info(f"Code explanation: {explanation}")
        elif(event_type == "code_improve"):
            code = data.get("code", "")
            improved = await self._improve(code)
            logger.info(f"Improved code: {improved}")
