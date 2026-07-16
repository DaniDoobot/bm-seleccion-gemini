"""
bm-seleccion-gemini — Boston Medical Group Candidate Selection Voice Service.

FastAPI application entry point.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers.health import router as health_router
from app.routers.voice import router as voice_router


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _read_version() -> str:
    version_file = Path(__file__).parent / "version.txt"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup validation and graceful shutdown."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    logger.info(
        "bm-seleccion-gemini v%s starting. model=%s voice=%s",
        _read_version(),
        settings.gemini_model,
        settings.gemini_voice_name,
    )

    if not settings.gemini_api_key:
        logger.warning(
            "GEMINI_API_KEY is not set. "
            "The /voice/stream endpoint will reject connections until it is configured."
        )

    yield

    logger.info("bm-seleccion-gemini shutting down.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="BM Selección Gemini",
        description=(
            "Boston Medical Group — Candidate Selection Voice Service. "
            "Powered by Gemini Live real-time audio streaming."
        ),
        version=_read_version(),
        lifespan=lifespan,
        # Disable automatic /docs and /redoc in production if needed
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(voice_router)

    return app


app = create_app()
