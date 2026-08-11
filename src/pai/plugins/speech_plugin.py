import asyncio
from typing import Dict, Any, List

from polars import struct
import pvporcupine
import pyaudio
from loguru import logger
from pai.plugins.base_plugin import BasePlugin

class SpeechPlugin(BasePlugin):
    def __init__(self):
        super().__init__()
        self.name = "speech"
    
    async def initialize(self) -> None:
        logger.info("SpeechPlugin initialized")
    
        self.porcupine = pvporcupine.create(keywords=["hey pAI"],access_key="YOUR_PORCUP")
        self.pa = pyaudio.PyAudio()
        self.audio_stream = self.pa.open(
            rate=self.porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.porcupine.frame_length
        )
        self.wake_word_detected = False
    
    async def listen_for_wake_word(self, callback):
        while True:
            pcm = self.audio_stream.read(self.porcupine.frame_length)
            pcm = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
            keyword_index = self.porcupine.process(pcm)
            if keyword_index >= 0:
                await callback("wake_word_detected")
                await asyncio.sleep(1)  # debounce
                
    def get_capabilities(self) -> List[str]:
        return ["speech.stt", "speech.tts", "speech.wake_word"]
    
    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "speech.stt":
            return await self._stt(params.get("audio_data"))
        elif action == "speech.tts":
            return await self._tts(params.get("text"))
        elif action == "speech.wake_word":
            return await self._wake_word(params.get("audio_stream"))
        raise ValueError(f"Unknown speech action: {action}")
    
    async def _stt(self, audio_data: bytes) -> str:
        return "Transcribed text from audio"
    
    async def _tts(self, text: str) -> bytes:
        # Use Piper TTS
        try:
            import subprocess
            # Piper expects text on stdin, outputs raw audio
            proc = await asyncio.create_subprocess_exec(
                "piper", "--model", "/path/to/voice.onnx", "--output_raw",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await proc.communicate(text.encode())
            return stdout  # raw PCM audio (16kHz, mono, 16-bit)
        except FileNotFoundError:
            logger.warning("Piper not installed. Returning mock audio.")
            return b"mock_audio_data"
    
    async def _wake_word(self, audio_stream) -> bool:
        return "hey_pai" in str(audio_stream)