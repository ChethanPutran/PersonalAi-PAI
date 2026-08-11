import pytest
from fastapi.testclient import TestClient
from pai.main import app
import json
import asyncio

client = TestClient(app)

def test_e2e_goal_research():
    """Full flow: goal -> agent -> plugin -> result"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "search for Python 3.12 features", "context": {}})
        result = ws.receive_json()
        assert result["type"] == "result"
        assert "search" in str(result["data"]).lower()

def test_e2e_camera_vision():
    with open("tests/fixtures/test_image.jpg", "rb") as f:
        import base64
        img_b64 = base64.b64encode(f.read()).decode()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "goal": "What objects do you see?", "context": {"image_data": img_b64}})
        result = ws.receive_json()
        assert "person" in str(result["data"]).lower() or "object" in str(result["data"]).lower()

def test_e2e_speech_stt():
    # Mock audio
    audio_b64 = "base64_encoded_audio_here"
    with client.websocket_connect("/ws/stt") as ws:
        ws.send_json({"type": "audio_chunk", "data": audio_b64})
        transcript = ws.receive_json()
        assert "transcript" in transcript