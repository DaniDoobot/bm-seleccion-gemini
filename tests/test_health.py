"""Tests for the /health endpoint.

Validates that:
  - The endpoint returns HTTP 200.
  - The response body contains all required fields.
  - Sensitive values (API key) are never exposed.
  - Registered scenarios appear in the response.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")

from app.main import app

client = TestClient(app)


class TestHealthEndpoint:

    def test_health_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_status_is_ok(self):
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_contains_version(self):
        data = client.get("/health").json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert data["version"]  # not empty

    def test_health_contains_model(self):
        data = client.get("/health").json()
        assert "model" in data
        assert "gemini" in data["model"].lower()

    def test_health_contains_voice(self):
        data = client.get("/health").json()
        assert "voice" in data
        assert data["voice"] == "Algieba"

    def test_health_api_key_configured_is_boolean(self):
        data = client.get("/health").json()
        assert "api_key_configured" in data
        assert isinstance(data["api_key_configured"], bool)

    def test_health_does_not_expose_api_key_value(self):
        """The actual API key string must never appear in the response."""
        body = client.get("/health").text
        # The test key is "test-api-key-not-real" (from .env.test)
        assert "test-api-key-not-real" not in body

    def test_health_does_not_expose_gemini_url_with_key(self):
        """The full Gemini WebSocket URL (which contains the key) must not appear."""
        body = client.get("/health").text
        assert "generativelanguage.googleapis.com" not in body

    def test_health_contains_scenarios(self):
        data = client.get("/health").json()
        assert "scenarios" in data
        assert isinstance(data["scenarios"], list)
        assert len(data["scenarios"]) >= 2

    def test_health_scenarios_contain_expected_ids(self):
        data = client.get("/health").json()
        ids = {s["id"] for s in data["scenarios"]}
        assert "seleccion_1" in ids
        assert "seleccion_2" in ids

    def test_health_scenarios_have_name(self):
        data = client.get("/health").json()
        for sc in data["scenarios"]:
            assert "id" in sc
            assert "name" in sc
            assert sc["name"]  # not empty

    def test_health_response_has_no_extra_sensitive_fields(self):
        """Verify that common sensitive field names do not appear."""
        data = client.get("/health").json()
        sensitive_keys = {"api_key", "secret", "token", "password", "key"}
        response_keys = {k.lower() for k in data.keys()}
        intersection = sensitive_keys & response_keys
        assert not intersection, f"Sensitive keys found in health response: {intersection}"

    def test_health_does_not_contain_prompt_content(self):
        """Verify that the health check response never leaks raw scenario prompt instructions."""
        body = client.get("/health").text
        assert "MIGUEL PÉREZ GÓMEZ" not in body
        assert "save_candidate_context" not in body
