class ConversationAgent(BaseAgent):
    async def process_goal(self, goal, context):
        # Maintain conversation history
        history = context.get("history", [])
        prompt = f"Conversation history:\n{history}\nUser: {goal}\nAssistant:"
        response = await self.use_plugin("llm", "complete", {"prompt": prompt})
        return {"response": response["text"]}