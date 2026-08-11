from phue import Bridge  # Philips Hue
from pai.plugins.base_plugin import BasePlugin

class SmartHomePlugin(BasePlugin):
    name = "smart_home"
    
    async def initialize(self):
        self.bridge = Bridge(os.getenv("HUE_BRIDGE_IP"))
        self.bridge.connect()
    
    def get_capabilities(self):
        return ["smart_home.turn_on_light", "smart_home.set_brightness"]
    
    async def execute(self, action, params):
        if action == "smart_home.turn_on_light":
            light = self.bridge.get_light(params["light_id"])
            light.on = True
            return {"status": "on"}
        elif action == "smart_home.set_brightness":
            light = self.bridge.get_light(params["light_id"])
            light.brightness = params["brightness"]
            return {"brightness": params["brightness"]}