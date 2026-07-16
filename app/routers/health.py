"""Health check router.

Returns service status, configured model and voice, and available scenarios.
Sensitive values (API keys, Gemini URLs) are never included in the response.
"""
import logging
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.scenarios.registry import list_scenarios

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


def _read_version() -> str:
    """Read the service version from version.txt next to the app package."""
    version_file = Path(__file__).parent.parent / "version.txt"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


@router.get("/health", summary="Health check")
async def health_check() -> JSONResponse:
    """Return operational status of the service.

    The response includes:
      - status: "ok" if the service is running.
      - version: service version string.
      - model: configured Gemini model name.
      - voice: configured Gemini voice name.
      - api_key_configured: boolean (True if key is set, no value exposed).
      - scenarios: list of registered scenario slugs and display names.

    Sensitive data (API keys, full Gemini WebSocket URLs) are never returned.
    """
    settings = get_settings()

    scenarios_info = [
        {"id": sc.scenario_id, "name": sc.display_name}
        for sc in list_scenarios()
    ]

    return JSONResponse(
        content={
            "status": "ok",
            "version": _read_version(),
            "model": settings.gemini_model,
            "voice": settings.gemini_voice_name,
            "api_key_configured": bool(settings.gemini_api_key),
            "scenarios": scenarios_info,
        }
    )
