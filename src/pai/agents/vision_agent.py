class VisionAgent(BaseAgent):
    name = "vision_agent"
    
    async def process_goal(self, goal: str, context: Dict) -> Dict:
        image = context.get('image')
        if "detect" in goal.lower():
            return await self._detect_objects(image)
        elif "read" in goal.lower() or "ocr" in goal.lower():
            return await self._ocr(image)
        elif "describe" in goal.lower():
            return await self._describe_scene(image)
        return {"error": "Unknown vision goal"}
    
    async def _detect_objects(self, image) -> List[Dict]:
        return await self.use_plugin("vision", "detect_objects", {"image_data": image})
    
    async def _ocr(self, image) -> str:
        return await self.use_plugin("vision", "ocr", {"image_data": image})
    
    async def _describe_scene(self, image) -> str:
        return await self.use_plugin("vision", "describe_scene", {"image_data": image})