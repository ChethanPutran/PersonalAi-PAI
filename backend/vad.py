import webrtcvad
import numpy as np
import collections
import wave
import io
from typing import Tuple, Optional, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VoiceActivityDetector:
    """
    Real-time Voice Activity Detection using WebRTC VAD
    Detects when user is speaking vs silence
    """
    
    def __init__(self, mode: int = 3, sample_rate: int = 16000, frame_duration_ms: int = 30):
        """
        Args:
            mode: Aggressiveness mode (0-3, 3 = most aggressive)
            sample_rate: Audio sample rate (8000, 16000, 32000, 48000)
            frame_duration_ms: Frame duration (10, 20, or 30 ms)
        """
        self.vad = webrtcvad.Vad(mode)
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        
        # State tracking
        self.speech_frames = []
        self.silence_frames = []
        self.is_speaking = False
        self.speech_confidence = 0.0
        
        # Thresholds
        self.speech_trigger_frames = 10  # 300ms of speech to trigger
        self.silence_trigger_frames = 15  # 450ms of silence to end
        self.min_speech_duration_ms = 500  # Minimum 500ms speech
        self.max_speech_duration_ms = 30000  # Maximum 30 seconds
        
        # Ring buffers for smooth detection
        self.recent_voiced = collections.deque(maxlen=20)
        self.recent_silence = collections.deque(maxlen=20)
    
    def is_speech_frame(self, audio_bytes: bytes) -> bool:
        """Check if a single frame contains speech"""
        try:
            return self.vad.is_speech(audio_bytes, self.sample_rate)
        except Exception as e:
            logger.error(f"VAD frame error: {e}")
            return False
    
    def process_chunk(self, audio_chunk: bytes) -> Tuple[bool, float]:
        """
        Process audio chunk and return speaking status and confidence
        
        Returns:
            (is_speaking, confidence) where confidence is 0-1
        """
        # Ensure chunk is correct size
        if len(audio_chunk) != self.frame_size * 2:  # 16-bit = 2 bytes per sample
            # Pad or truncate
            if len(audio_chunk) < self.frame_size * 2:
                audio_chunk += b'\x00' * (self.frame_size * 2 - len(audio_chunk))
            else:
                audio_chunk = audio_chunk[:self.frame_size * 2]
        
        is_voiced = self.is_speech_frame(audio_chunk)
        
        # Update buffers
        self.recent_voiced.append(is_voiced)
        self.recent_silence.append(not is_voiced)
        
        # Calculate confidence (percentage of voiced frames in recent history)
        voiced_count = sum(self.recent_voiced)
        self.speech_confidence = voiced_count / len(self.recent_voiced) if self.recent_voiced else 0
        
        # State machine for speech detection
        if not self.is_speaking:
            # Check if we should start speaking
            if voiced_count >= self.speech_trigger_frames:
                self.is_speaking = True
                self.speech_frames = []
                logger.info("Speech started")
        else:
            # Check if we should stop speaking
            silence_count = sum(self.recent_silence)
            if silence_count >= self.silence_trigger_frames:
                self.is_speaking = False
                logger.info("Speech ended")
                return False, self.speech_confidence
        
        return self.is_speaking, self.speech_confidence
    
    def extract_speech_segment(self, audio_data: bytes) -> Optional[bytes]:
        """
        Extract continuous speech segment from audio stream
        Returns speech audio bytes when speech ends, None otherwise
        """
        if self.is_speaking:
            self.speech_frames.append(audio_data)
            
            # Check max duration
            if len(self.speech_frames) * self.frame_duration_ms >= self.max_speech_duration_ms:
                self.is_speaking = False
                logger.info("Max speech duration reached")
                return b''.join(self.speech_frames)
        
        elif self.speech_frames:
            # Speech just ended
            speech_duration = len(self.speech_frames) * self.frame_duration_ms
            if speech_duration >= self.min_speech_duration_ms:
                result = b''.join(self.speech_frames)
                self.speech_frames = []
                return result
            else:
                # Too short, discard
                self.speech_frames = []
                return None
        
        return None
    
    def reset(self):
        """Reset VAD state"""
        self.is_speaking = False
        self.speech_frames = []
        self.speech_confidence = 0.0
        self.recent_voiced.clear()
        self.recent_silence.clear()


class AudioPreprocessor:
    """Preprocess audio for better VAD performance"""
    
    @staticmethod
    def convert_to_mono(audio_data: bytes, channels: int = 2) -> bytes:
        """Convert stereo to mono by averaging channels"""
        if channels == 1:
            return audio_data
        
        # Convert to numpy array
        samples = np.frombuffer(audio_data, dtype=np.int16)
        # Reshape to (num_samples, channels)
        samples = samples.reshape(-1, channels)
        # Average across channels
        mono = np.mean(samples, axis=1).astype(np.int16)
        return mono.tobytes()
    
    @staticmethod
    def resample(audio_data: bytes, original_rate: int, target_rate: int = 16000) -> bytes:
        """Resample audio to target rate"""
        if original_rate == target_rate:
            return audio_data
        
        from scipy import signal
        
        samples = np.frombuffer(audio_data, dtype=np.int16)
        # Calculate resampling ratio
        num_samples = int(len(samples) * target_rate / original_rate)
        resampled = signal.resample(samples, num_samples).astype(np.int16)
        return resampled.tobytes()
    
    @staticmethod
    def normalize_volume(audio_data: bytes, target_db: float = -20.0) -> bytes:
        """Normalize audio volume"""
        samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
        
        # Calculate current RMS
        rms = np.sqrt(np.mean(samples**2))
        if rms > 0:
            # Calculate gain needed
            target_rms = 10 ** (target_db / 20) * 32768
            gain = target_rms / rms
            # Apply gain and clip
            samples = np.clip(samples * gain, -32768, 32767)
        
        return samples.astype(np.int16).tobytes()