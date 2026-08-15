from typing import Dict, Any
from loguru import logger
from pai.agents.base_agent import BaseAgent

class CommunicationAgent(BaseAgent):
    """Assists with translation, conversation, and summarization."""
    def __init__(self, kernel):
        super().__init__("communication_agent", kernel)
        
    async def initialize(self) -> None:
        self._capabilities = ["translate", "summarize", "suggest_response"]
        logger.info("CommunicationAgent initialized")
    
    async def process_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if "translate" in goal.lower():
            return await self._translate(context.get("text", ""), context.get("target_lang", "en"))
        elif "summarize" in goal.lower():
            return await self._summarize(context.get("text", ""))
        elif "suggest" in goal.lower():
            return await self._suggest_response(context.get("conversation", ""))
        return {"error": "Unsupported communication goal"}
    
    async def _translate(self, text: str, target_lang: str) -> Dict[str, Any]:
        translated = f"[Translated to {target_lang}] {text}"
        return {"original": text, "translated": translated, "language": target_lang}
    
    async def _summarize(self, text: str) -> Dict[str, Any]:
        summary = text[:100] + "..." if len(text) > 100 else text
        return {"summary": summary}
    
    async def _suggest_response(self, conversation: str) -> Dict[str, Any]:
        suggestions = ["That's interesting!", "Can you tell me more?", "I see."]
        return {"suggestions": suggestions}
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == "conversation.start":
            await self.use_plugin("speech", "listen", {})