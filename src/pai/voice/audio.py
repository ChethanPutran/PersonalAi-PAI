"""Small PCM helpers shared by the VAD, the providers and tests (numpy only)."""
from __future__ import annotations

from typing import Iterator

import numpy as np


def pcm_to_float(pcm: bytes) -> np.ndarray:
    """PCM16LE bytes -> float32 in [-1, 1]. A trailing odd byte is ignored."""
    n = len(pcm) // 2
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm[: n * 2], dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def rms(pcm: bytes) -> float:
    x = pcm_to_float(pcm)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of PCM16LE mono (fine for speech)."""
    if src_rate == dst_rate or not pcm:
        return pcm
    x = pcm_to_float(pcm)
    if x.size == 0:
        return b""
    n_out = max(1, int(round(x.size * dst_rate / src_rate)))
    positions = np.linspace(0, x.size - 1, num=n_out)
    return float_to_pcm(np.interp(positions, np.arange(x.size), x))


def frame_bytes(sample_rate: int, frame_ms: int) -> int:
    return sample_rate * frame_ms // 1000 * 2


def pcm_duration_ms(num_bytes: int, sample_rate: int) -> float:
    return num_bytes / 2 / sample_rate * 1000.0


def iter_chunks(pcm: bytes, size: int) -> Iterator[bytes]:
    size -= size % 2
    for i in range(0, len(pcm), size):
        yield pcm[i : i + size]


def sine(freq: float, ms: int, rate: int, amplitude: float = 0.3) -> bytes:
    t = np.arange(int(rate * ms / 1000)) / rate
    return float_to_pcm(amplitude * np.sin(2 * np.pi * freq * t).astype(np.float32))
