# Plugins

Plugins are modular capabilities that extend the system.

## Plugin categories

| Category | Purpose |
|---|---|
| Sensor plugins | Camera, microphone, GPS, device sensors |
| Intelligence plugins | OCR, translation, summarization, vision |
| Action plugins | Browser automation, app control, workflow execution |
| Integration plugins | Calendar, Gmail, Maps, storage |

## Current code mapping

The repo currently exposes small tool-style capabilities through `src/tools.py` and `src/tools2.py`, plus reusable skills under `src/skills/`.

## Design goal

Plugins should be replaceable, isolated, permission-aware, and event-driven.
