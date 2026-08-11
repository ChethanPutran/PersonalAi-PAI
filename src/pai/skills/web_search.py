import requests
from typing import Dict, Any

def execute(city: str, api_key: str = None) -> Dict[str, Any]:
    """Get weather for a city"""
    try:
        # Using wttr.in for free weather (no API key needed)
        response = requests.get(f"https://wttr.in/{city}?format=%C+%t")
        weather = response.text.strip()
        return {"city": city, "weather": weather}
    except Exception as e:
        return {"error": str(e)}