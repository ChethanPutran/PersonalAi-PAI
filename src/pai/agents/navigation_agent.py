class NavigationAgent(BaseAgent):
    name = "navigation_agent"
    
    async def process_goal(self, goal: str, context: Dict) -> Dict:
        if "route" in goal.lower():
            return await self._plan_route(context)
        elif "eta" in goal.lower():
            return await self._get_eta(context)
        return {"error": "Unknown navigation goal"}
    
    async def _plan_route(self, context: Dict) -> Dict:
        return await self.use_plugin("maps", "get_route", context)
    
    async def _get_eta(self, context: Dict) -> Dict:
        return await self.use_plugin("maps", "get_eta", context)