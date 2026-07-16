"""Tests for voice WebSocket routing, scenario resolution, and API key protection."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app

client = TestClient(app)


def test_websocket_rejects_unknown_scenario():
    """Verify that an unknown scenario slug rejects the connection with code 1008."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/voice/stream?scenario=non_existent_scenario") as websocket:
            websocket.receive_text()
    assert exc_info.value.code == 1008


def test_websocket_rejects_empty_scenario():
    """Verify that an empty scenario slug rejects the connection with code 1008."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/voice/stream?scenario=") as websocket:
            websocket.receive_text()
    assert exc_info.value.code == 1008


def test_websocket_rejects_missing_api_key(monkeypatch):
    """Verify that if GEMINI_API_KEY is missing, the connection is rejected with 1008."""
    from app.config import Settings
    
    # Force settings with empty API key
    def mock_get_settings():
        return Settings(gemini_api_key="", env_file=None)
        
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
            websocket.receive_text()
    assert exc_info.value.code == 1008
