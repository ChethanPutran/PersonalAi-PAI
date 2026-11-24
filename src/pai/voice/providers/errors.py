"""Errors raised by voice providers."""
from __future__ import annotations


class VoiceProviderError(RuntimeError):
    """A provider call failed (network, upstream error status, bad audio...)."""


class VoiceConfigurationError(VoiceProviderError):
    """The voice configuration cannot be used (e.g. a required API key is missing)."""
