from typing import Dict, Any, List
from loguru import logger
from pai.agents.base_agent import BaseAgent

class TravelAgent(BaseAgent):
    """Handles route planning, taxi booking, public transit."""
    
    def __init__(self, kernel=None):
        super().__init__("travel_agent", kernel)
        self._capabilities = [
            "travel.route_plan",
            "travel.book_taxi",
            "travel.get_eta",
            "travel.find_public_transit"
        ]
    
    async def initialize(self) -> None:
        logger.info("TravelAgent initialized")
    
    async def process_goal(self, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if "route" in goal.lower() or "navigate" in goal.lower():
            return await self._route_plan(goal, context)
        elif "taxi" in goal.lower() or "cab" in goal.lower() or "uber" in goal.lower():
            return await self._book_taxi(goal, context)
        elif "eta" in goal.lower():
            return await self._get_eta(context)
        elif "bus" in goal.lower() or "train" in goal.lower():
            return await self._public_transit(goal, context)
        return {"error": "Unsupported travel request"}
    
    async def _route_plan(self, goal: str, context: Dict) -> Dict:
        origin = context.get("origin", "current_location")
        destination = context.get("destination", "")
        # Use Maps plugin
        route = await self.use_plugin("maps", "get_route", {"origin": origin, "destination": destination})
        return {"route": route, "duration": route.get("duration"), "distance": route.get("distance")}
    
    async def _book_taxi(self, goal: str, context: Dict) -> Dict:
        pickup = context.get("pickup", "current_location")
        dropoff = context.get("dropoff")
        # Use transport plugin (Uber/Ola)
        booking = await self.use_plugin("transport", "book_ride", {
            "pickup": pickup, "dropoff": dropoff, "provider": context.get("provider", "uber")
        })
        return {"booking_id": booking.get("id"), "eta": booking.get("eta"), "price": booking.get("price")}
    
    async def _get_eta(self, context: Dict) -> Dict:
        eta = await self.use_plugin("maps", "get_eta", {"destination": context.get("destination")})
        return eta
    
    async def _public_transit(self, goal: str, context: Dict) -> Dict:
        transit = await self.use_plugin("maps", "public_transit", context)
        return transit
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type == "location.update":
            # Recalculate ETA if needed
            pass