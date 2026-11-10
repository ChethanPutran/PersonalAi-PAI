
from .base import BaseAgent


class ConversationAgent(BaseAgent):
    name = "conversation_agent"
    def __init__(self):
        super().__init__()

    async def initialize(self):
        """Initialize the conversation agent."""
        return None

    async def handle_event(self, event):
        """Handle a conversation event using its goal and context."""
        if isinstance(event, dict):
            goal = event.get("goal", event.get("message", ""))
            context = event.get("context", {})
        else:
            goal = event
            context = {}
        return await self.process_goal(goal, context)

    async def process_goal(self, goal, context):
        # Maintain conversation history
        history = context.get("history", [])
        prompt = f"Conversation history:\n{history}\nUser: {goal}\nAssistant:"
        response = await self.use_plugin("llm", "complete", {"prompt": prompt})
        return {"response": response["text"]}