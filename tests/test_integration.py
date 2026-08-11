import pytest
from pai.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Personal AI System"
    assert data["status"] == "running"

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_websocket_goal():
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "goal", "goal": "test integration", "context": {}})
        result = websocket.receive_json()
        assert result["type"] == "result"