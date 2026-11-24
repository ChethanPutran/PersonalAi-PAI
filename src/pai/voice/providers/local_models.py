"""Locate (and, if allowed, download) the model files of the free local engines.

Downloads are restricted to fixed, well-known HTTPS locations and written atomically, so a
failed or interrupted download never leaves a half-written model that later "loads".
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

from pai.voice.providers.errors import VoiceConfigurationError

_NAME = re.compile(r"^[A-Za-z0-9._-]{3,80}$")           # a model *name*, never a path
VOSK_URL = "https://alphacephei.com/vosk/models/{name}.zip"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{locale}/{speaker}/{quality}/{name}{ext}"
_ALLOWED_HOSTS = {"alphacephei.com", "huggingface.co"}


def _check_name(name: str) -> str:
    if not _NAME.match(name) or ".." in name:
        raise VoiceConfigurationError(f"invalid model name: {name!r}")
    return name


def _download(url: str, dest: Path) -> None:
    if urlparse(url).hostname not in _ALLOWED_HOSTS or not url.startswith("https://"):
        raise VoiceConfigurationError(f"refusing to download from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 16):
                out.write(chunk)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def ensure_vosk_model(models_dir: str, name: str, *, download: bool) -> Path:
    name = _check_name(name)
    target = Path(models_dir) / name
    if (target / "am").exists() or (target / "conf").exists():
        return target
    if not download:
        raise VoiceConfigurationError(
            f"Vosk model '{name}' not found in {models_dir}. Download it from "
            f"{VOSK_URL.format(name=name)} and unzip it there, or set voice.auto_download_models: true.")
    logger.info("Downloading Vosk model {} (one time)…", name)
    zip_path = Path(models_dir) / f"{name}.zip"
    _download(VOSK_URL.format(name=name), zip_path)
    try:
        root = Path(models_dir).resolve()
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():                          # zip-slip guard
                if not (root / member).resolve().is_relative_to(root):
                    raise VoiceConfigurationError("model archive has unsafe paths")
            z.extractall(root)
    finally:
        zip_path.unlink(missing_ok=True)
    if not target.exists():
        raise VoiceConfigurationError(f"archive did not contain the expected folder '{name}'")
    return target


def ensure_piper_voice(models_dir: str, name: str, *, download: bool) -> Path:
    """Returns the .onnx path (its .onnx.json config sits next to it)."""
    name = _check_name(name)
    onnx = Path(models_dir) / f"{name}.onnx"
    if onnx.exists() and Path(f"{onnx}.json").exists():
        return onnx
    m = re.match(r"^([a-z]{2})_([A-Z]{2})-(.+)-(x_low|low|medium|high)$", name)
    if not m or not download:
        raise VoiceConfigurationError(
            f"Piper voice '{name}' not found in {models_dir}"
            + ("" if m else " (expected a name like en_US-lessac-medium)")
            + ". Get it from https://huggingface.co/rhasspy/piper-voices or set voice.auto_download_models: true.")
    lang, country, speaker, quality = m.groups()
    logger.info("Downloading Piper voice {} (one time)…", name)
    for ext in (".onnx", ".onnx.json"):
        _download(PIPER_URL.format(lang=lang, locale=f"{lang}_{country}", speaker=speaker,
                                   quality=quality, name=name, ext=ext), Path(models_dir) / f"{name}{ext}")
    return onnx
