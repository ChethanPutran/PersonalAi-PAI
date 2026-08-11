from typing import Dict, List

import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta
from pai.agents.base_agent import BaseAgent

class LearningAgent(BaseAgent):
    """Learns user preferences and adapts behavior."""
    name = "learning_agent"


    async def _learn(self, feedback: Dict) -> Dict:
        await self.use_plugin("learning", "record_interaction", {"action": feedback.get('action'), "outcome": feedback.get('result')})
        return {"learned": True}
    
    async def _suggest(self, context: Dict) -> List[str]:
        suggestions = await self.use_plugin("learning", "recommend", {"context": context.get('situation', '')})
        return {"suggestions": suggestions}
    
    async def initialize(self):
        self.user_actions = defaultdict(list)  # action -> list of timestamps
        self.sequence_model = None  # would use LSTM in production
    
    async def process_goal(self, goal: str, context: Dict) -> Dict:
        if "predict" in goal.lower():
            return await self._predict_next_action(context)
        elif "recommend" in goal.lower():
            return await self._recommend(context.get("situation", ""))
        return {"error": "Unknown learning goal"}
    
    async def _record_action(self, action: str):
        self.user_actions[action].append(datetime.utcnow())
        # Keep only last 30 days
        cutoff = datetime.utcnow() - timedelta(days=30)
        self.user_actions[action] = [t for t in self.user_actions[action] if t > cutoff]
    
    async def _predict_next_action(self, context: Dict) -> Dict:
        # Simple Markov chain prediction
        last_action = context.get("last_action")
        if not last_action:
            return {"prediction": "unknown"}
        # Find most common action following last_action (simulated)
        # In production, use a sequence model
        return {"prediction": "check_email", "confidence": 0.7}
    
    async def _recommend(self, situation: str) -> List[str]:
        # Recommend based on time of day
        hour = datetime.utcnow().hour
        if 6 <= hour < 12:
            return ["Check calendar", "Read news"]
        elif 12 <= hour < 14:
            return ["Take a break", "Order lunch"]
        else:
            return ["Plan tomorrow", "Review tasks"]
    
    async def handle_event(self, event_type: str, data: Dict):
        if event_type == "user.action":
            await self._record_action(data["action"])
