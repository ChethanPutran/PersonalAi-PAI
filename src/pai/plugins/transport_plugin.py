import os

import requests
from typing import Dict, Any, List
from pai.plugins.base_plugin import BasePlugin
import aiohttp

class TransportPlugin(BasePlugin):
    """Uber / Ola ride booking."""
    name = "transport"

    def __init__(self):
        super().__init__()
        self.uber_api_key = None
        self.uber_client_id = None
        self.uber_client_secret = None
        self.access_token = None

        
    async def initialize(self):
        # Load from config
        self.uber_api_key = os.getenv("UBER_API_KEY")
        self.uber_client_id = os.getenv("UBER_CLIENT_ID")
        self.uber_client_secret = os.getenv("UBER_CLIENT_SECRET")
        await self._refresh_token()
    
    def get_capabilities(self) -> List[str]:
        return ["transport.book_ride", "transport.estimate_price", "transport.get_ride_status"]
    
    async def _book_ride(self, params: Dict) -> Dict:
        # POST to Uber API
        return {'ride_id': 'ride123', 'eta': 5, 'price': '$15.00', 'driver': 'John'}
    
    async def _estimate_price(self, params: Dict) -> Dict:
        return {'low_estimate': 12.00, 'high_estimate': 18.00, 'currency': 'USD'}
    
    async def _get_ride_status(self, params: Dict) -> Dict:
        return {'status': 'en_route', 'driver_location': {'lat': 37.7749, 'lng': -122.4194}}
    
    async def _refresh_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://login.uber.com/oauth/v2/token",
                data={
                    "client_id": self.uber_client_id,
                    "client_secret": self.uber_client_secret,
                    "grant_type": "client_credentials",
                    "scope": "request.ride"
                }
            ) as resp:
                data = await resp.json()
                self.access_token = data["access_token"]
    
    async def execute(self, action: str, params: Dict) -> Any:
        if action == "transport.book_ride":
            return await self._book_ride_real(params)
        elif action == "transport.estimate_price":
            return await self._estimate_price_real(params)
        raise ValueError(f"Unknown action: {action}")
    
    async def _book_ride_real(self, params: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            data = {
                "start_latitude": params["pickup_lat"],
                "start_longitude": params["pickup_lng"],
                "end_latitude": params["dropoff_lat"],
                "end_longitude": params["dropoff_lng"],
                "product_id": params.get("product_id", "uberX")
            }
            async with session.post("https://api.uber.com/v1.2/requests", json=data, headers=headers) as resp:
                result = await resp.json()
                return {"ride_id": result["request_id"], "status": result["status"], "eta": result["eta"]}
    
    async def _estimate_price_real(self, params: Dict) -> Dict:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            params = {
                "start_latitude": params["pickup_lat"],
                "start_longitude": params["pickup_lng"],
                "end_latitude": params["dropoff_lat"],
                "end_longitude": params["dropoff_lng"]
            }
            async with session.get("https://api.uber.com/v1.2/estimates/price", params=params, headers=headers) as resp:
                data = await resp.json()
                return {"low_estimate": data["prices"][0]["low_estimate"], "high_estimate": data["prices"][0]["high_estimate"]}