from typing import Any

from pai.plugins.base_plugin import BasePlugin

class MeetingPlugin(BasePlugin):
    async def summarize(self, audio_file: str) -> str:
        # Use Whisper for transcription
        transcript = await self._transcribe(audio_file)
        # Use LLM to summarise
        summary = await self.use_plugin("llm", "complete", {"prompt": f"Summarize this meeting transcript:\n{transcript}"})
        return summary["text"]
    
    async def _transcribe(self, audio_file: str) -> str:
        # Placeholder for actual transcription logic using Whisper
        return "This is a transcribed text of the meeting."
    
    def get_capabilities(self):
        return ["meeting.summarize"]
    
    async def execute(self, action, params)-> Any:
        if action == "meeting.summarize":
            return {"summary": await self.summarize(params["audio_file"])}
        
    async def on_event(self, event_name, data):
        if event_name == "meeting.audio_recorded":
            summary = await self.summarize(data["audio_file"])
            print(f"Meeting Summary: {summary}")    

    async def use_plugin(self, plugin_name: str, action: str, params: dict) -> Any:
        # This method will be overridden by the system to allow calling other plugins
        pass