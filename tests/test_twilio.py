"""Tests for Twilio integration endpoints and refactored scenario WebSocket routing."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import xml.etree.ElementTree as ET

from app.main import app
from app.config import Settings

client = TestClient(app)


def test_incoming_call_returns_twiml_xml_seleccion_1(monkeypatch):
    """Verify GET and POST /voice/incoming/seleccion_1 returns 200, XML content type and TwiML structure."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            public_ws_base_url="wss://bmseleccion-backend.doobot.ai/voice/stream",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    for method in ["get", "post"]:
        response = getattr(client, method)("/voice/incoming/seleccion_1")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]

        # Parse XML
        root = ET.fromstring(response.content)
        assert root.tag == "Response"
        
        connect = root.find("Connect")
        assert connect is not None
        
        stream = connect.find("Stream")
        assert stream is not None
        assert stream.get("url") == "wss://bmseleccion-backend.doobot.ai/voice/stream/seleccion_1"
        
        hangup = root.find("Hangup")
        assert hangup is not None


def test_incoming_call_returns_twiml_xml_seleccion_2(monkeypatch):
    """Verify GET and POST /voice/incoming/seleccion_2 returns 200, XML content type and TwiML structure."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            public_ws_base_url="wss://bmseleccion-backend.doobot.ai/voice/stream",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    for method in ["get", "post"]:
        response = getattr(client, method)("/voice/incoming/seleccion_2")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]

        # Parse XML
        root = ET.fromstring(response.content)
        assert root.tag == "Response"
        
        connect = root.find("Connect")
        assert connect is not None
        
        stream = connect.find("Stream")
        assert stream is not None
        assert stream.get("url") == "wss://bmseleccion-backend.doobot.ai/voice/stream/seleccion_2"
        
        hangup = root.find("Hangup")
        assert hangup is not None


def test_incoming_call_returns_404_for_unknown_scenario(monkeypatch):
    """Verify that requesting an invalid scenario returns 404."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            public_ws_base_url="wss://bmseleccion-backend.doobot.ai/voice/stream",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    response = client.post("/voice/incoming/escenario_inexistente")
    assert response.status_code == 404
    assert response.json()["detail"] == "Scenario not found"


def test_incoming_call_returns_500_when_url_missing(monkeypatch):
    """Verify that requesting TwiML when PUBLIC_WS_BASE_URL is not configured returns 500 error."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            public_ws_base_url="",  # missing
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    response = client.post("/voice/incoming/seleccion_1")
    assert response.status_code == 500
    assert "PUBLIC_WS_BASE_URL is not configured" in response.json()["detail"]


def test_websocket_path_routing_rejects_unknown(monkeypatch):
    """Verify that path-based WebSocket connection rejects unknown scenario with 1008."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/voice/stream/non_existent_scenario") as websocket:
            websocket.receive_text()
    assert exc_info.value.code == 1008


def test_websocket_path_routing_accepts_seleccion_1(monkeypatch):
    """Verify that path-based WebSocket connects and checks API key."""
    # If API key is empty, it will close with 1008
    def mock_get_settings():
        return Settings(gemini_api_key="", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/voice/stream/seleccion_1") as websocket:
            websocket.receive_text()
    assert exc_info.value.code == 1008
