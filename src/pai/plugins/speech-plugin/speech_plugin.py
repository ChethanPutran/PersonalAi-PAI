import asyncio
import os
import subprocess
from typing import Dict, Any, List

from loguru import logger
from precise_runner import PreciseRunner
from precise_runner.runner import ListenerEngine

from pai.plugins.base_plugin import BasePlugin


class SpeechPlugin(BasePlugin):
    def __init__(self):
        super().__init__()
        self.name = "speech"
        self._loop = None          # will be set in initialize
        self.runner = None
        self.engine = None

    async def initialize(self) -> None:
        """Initialize the speech plugin with Precise wake‑word engine."""
        self._loop = asyncio.get_running_loop()
        
        # Path to a Precise model file (download from Mycroft's repository)
        model_path = os.getenv("PRECISE_MODEL", "models/hey-jarvis.pb")
        if not os.path.exists(model_path):
            logger.warning(f"Precise model not found at {model_path}. "
                           "Wake‑word detection will be disabled.")
            self.runner = None
            return

        try:
            self.engine = ListenerEngine(model_path)
            self.runner = PreciseRunner(
                self.engine,
                on_activation=self._on_wake_word,
                sensitivity=0.5,        # 0..1, higher = more sensitive
                trigger_level=3         # consecutive detections before firing
            )
            self.runner.start()
            logger.info(f"SpeechPlugin initialized with Precise model: {model_path}")
        except Exception as e:
            logger.error(f"Failed to initialize Precise: {e}")
            self.runner = None

    async def start(self) -> None:
        self._running = True
        logger.info("SpeechPlugin started")

    async def shutdown(self) -> None:
        """Cleanly stop the Precise runner and release resources."""
        if self.runner:
            self.runner.stop()
        self._running = False
        logger.info("SpeechPlugin shut down")

    def _on_wake_word(self):
        """Called by PreciseRunner in a background thread."""
        # Schedule the async handler on the plugin's event loop
        asyncio.run_coroutine_threadsafe(
            self._wake_detected(),
            self._loop
        )

    async def _wake_detected(self):
        """Handle wake‑word detection asynchronously."""
        logger.info("Wake word detected!")
        # You can publish an event or call any callback here
        await self.handle_event("wake_word_detected", {"timestamp": asyncio.get_event_loop().time()})

    # --------------------- Capabilities ---------------------
    def get_capabilities(self) -> List[str]:
        return ["speech.stt", "speech.tts", "speech.wake_word"]

    async def execute(self, action: str, params: Dict[str, Any]) -> Any:
        if action == "speech.stt":
            return await self._stt(params.get("audio_data"))
        elif action == "speech.tts":
            return await self._tts(params.get("text"))
        elif action == "speech.wake_word":
            # This is now handled by the runner; this stub returns the runner state
            return {"wake_word_active": self.runner is not None and self.runner.is_running}
        raise ValueError(f"Unknown speech action: {action}")

    # --------------------- STT / TTS stubs ---------------------
    async def _stt(self, audio_data: bytes) -> str:
        # Replace with your actual STT implementation (e.g., Vosk, Whisper)
        return "Transcribed text from audio"

    async def _tts(self, text: str) -> bytes:
        # Example using Piper TTS (open source)
        try:
            proc = await asyncio.create_subprocess_exec(
                "piper", "--model", "/path/to/voice.onnx", "--output_raw",
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, _ = await proc.communicate(text.encode())
            return stdout  # raw PCM audio (16kHz, mono, 16‑bit)
        except FileNotFoundError:
            logger.warning("Piper not installed. Returning mock audio.")
            return b"mock_audio_data"

    async def _wake_word(self, audio_stream) -> bool:
        # This method is kept for compatibility; actual detection is handled by the runner.
        # You can optionally implement a fallback if the runner isn't active.
        return self.runner is not None and self.runner.is_running

    # --------------------- Permissions & Events ---------------------
    async def check_permissions(self, action: str) -> bool:
        # For simplicity, allow all actions. Implement your own logic if needed.
        return True

    async def handle_event(self, event: str, data: Dict[str, Any]) -> None:
        logger.info(f"Speech plugin received event: {event} with data: {data}")
        # Here you can react to events (e.g., start listening after wake word)
        if event == "wake_word_detected":
            # For example, trigger a voice interaction
            logger.info("👋 Wake word triggered – ready to accept commands")