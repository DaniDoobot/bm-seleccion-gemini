"""
Application configuration.

All settings are read from environment variables or a .env file.
Sensitive values (API keys, tokens) must NEVER be hardcoded here.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google Gemini ─────────────────────────────────────────────────────────
    gemini_api_key: str = ""
    gemini_model: str = "models/gemini-3.1-flash-live-preview"
    gemini_voice_name: str = "Algieba"
    gemini_thinking_level: str = "minimal"

    # ── VAD — Voice Activity Detection ────────────────────────────────────────
    vad_silence_duration_ms: int = 130
    vad_prefix_padding_ms: int = 120

    # ── Server ────────────────────────────────────────────────────────────────
    port: int = 8000
    public_url: str = ""
    public_ws_base_url: str = ""
    public_http_base_url: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "*"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_transcripts: bool = False

    # ── Twilio ────────────────────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # ── n8n ───────────────────────────────────────────────────────────────────
    n8n_events_webhook_url: str = ""
    n8n_webhook_token: str = ""

    # ── Flags ─────────────────────────────────────────────────────────────────
    call_recording_enabled: bool = True
    gemini_transcription_enabled: bool = True

    @property
    def allowed_origins(self) -> List[str]:
        """Return list of allowed CORS origins parsed from the env value."""
        raw = self.cors_origins.strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def gemini_ws_url(self) -> str:
        """
        WebSocket URL for Gemini Live BidiGenerateContent.
        The API key is appended at runtime and must NEVER be logged or exposed.
        """
        base = (
            "wss://generativelanguage.googleapis.com"
            "/ws/google.ai.generativelanguage.v1beta"
            ".GenerativeService.BidiGenerateContent"
        )
        return f"{base}?key={self.gemini_api_key}"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    import os
    import sys

    env_file = ".env"
    is_test_run = (
        os.environ.get("APP_ENV") == "test"
        or "PYTEST_CURRENT_TEST" in os.environ
        or (bool(sys.argv) and "pytest" in sys.argv[0].lower())
    )
    if is_test_run and os.path.exists(".env.test"):
        env_file = ".env.test"

    return Settings(_env_file=env_file)
