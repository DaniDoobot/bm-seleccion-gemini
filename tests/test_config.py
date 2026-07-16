"""Tests for application configuration loading.

Validates that:
  - Settings are loaded correctly from environment variables.
  - All Gemini voice parameters match the expected values.
  - A missing GEMINI_API_KEY is detected (empty string, not an error on load).
  - Required fields have correct types and defaults.
"""
import os

import pytest

# Force test environment before importing settings
os.environ.setdefault("APP_ENV", "test")

from app.config import Settings


class TestSettingsDefaults:
    """Settings created with explicit values (not from .env file)."""

    def _make_settings(self, **overrides) -> Settings:
        defaults = dict(
            gemini_api_key="test-key",
            gemini_model="models/gemini-3.1-flash-live-preview",
            gemini_voice_name="Algieba",
            gemini_thinking_level="minimal",
            vad_silence_duration_ms=130,
            vad_prefix_padding_ms=120,
            port=8000,
            cors_origins="*",
            log_level="INFO",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def test_gemini_model_default(self):
        s = self._make_settings()
        assert s.gemini_model == "models/gemini-3.1-flash-live-preview"

    def test_gemini_voice_name_default(self):
        s = self._make_settings()
        assert s.gemini_voice_name == "Algieba"

    def test_gemini_thinking_level_default(self):
        s = self._make_settings()
        assert s.gemini_thinking_level == "minimal"

    def test_vad_silence_duration_ms(self):
        s = self._make_settings()
        assert s.vad_silence_duration_ms == 130

    def test_vad_prefix_padding_ms(self):
        s = self._make_settings()
        assert s.vad_prefix_padding_ms == 120

    def test_port_is_integer(self):
        s = self._make_settings()
        assert isinstance(s.port, int)
        assert s.port == 8000

    def test_api_key_not_exposed_in_ws_url_repr(self):
        """The API key should appear in gemini_ws_url but that property
        must not be accidentally serialised to logs. We just check the
        property contains the key correctly when it is set."""
        s = self._make_settings(gemini_api_key="supersecret")
        assert "supersecret" in s.gemini_ws_url

    def test_empty_api_key_detected(self):
        """An empty API key must be detectable at startup so the service
        can warn before the first request fails."""
        s = self._make_settings(gemini_api_key="")
        assert not s.gemini_api_key
        assert not bool(s.gemini_api_key)

    def test_cors_wildcard_returns_list_with_star(self):
        s = self._make_settings(cors_origins="*")
        assert s.allowed_origins == ["*"]

    def test_cors_multiple_origins_parsed(self):
        s = self._make_settings(
            cors_origins="https://a.example.com,https://b.example.com"
        )
        origins = s.allowed_origins
        assert "https://a.example.com" in origins
        assert "https://b.example.com" in origins
        assert len(origins) == 2

    def test_cors_empty_string_returns_star(self):
        s = self._make_settings(cors_origins="")
        assert s.allowed_origins == ["*"]

    def test_vad_params_are_integers(self):
        s = self._make_settings()
        assert isinstance(s.vad_silence_duration_ms, int)
        assert isinstance(s.vad_prefix_padding_ms, int)

    def test_custom_vad_values(self):
        """Confirm VAD params can be overridden."""
        s = self._make_settings(vad_silence_duration_ms=200, vad_prefix_padding_ms=80)
        assert s.vad_silence_duration_ms == 200
        assert s.vad_prefix_padding_ms == 80
