"""Integration test script for Gemini Live real-time voice connection.

This script validates that:
  - GEMINI_API_KEY is valid and active.
  - The configured Gemini Live model accepts the connection.
  - The voice (Algieba) and VAD parameters are accepted.
  - Gemini generates audio in response to a text turn.
  - Audio can be converted from PCM 24 kHz to µ-law 8 kHz without errors.
  - The session opens and closes cleanly.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_gemini_live.py

The script writes two WAV files to tmp/audio-tests/:
  - gemini_output_24khz.wav  — raw Gemini PCM output at 24 kHz
  - twilio_output_8khz.wav   — audio after Twilio conversion pipeline (8 kHz)

SECURITY:
  - The API key is never printed or logged.
  - Only model name, voice and parameter values are shown.
  - WAV files are written to a git-ignored directory.

NOTE:
  This script requires a real GEMINI_API_KEY and makes an actual API call.
  It is intentionally excluded from the automated pytest suite.
  Run it manually for integration validation only.
"""
import asyncio
import base64
import logging
import sys
import time
from pathlib import Path

# ── Windows console: force UTF-8 output ──────────────────────────────────────
# PowerShell on Windows may default to cp1252, which cannot encode box-drawing
# characters. Reconfigure stdout/stderr to UTF-8 before any print() call.
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Bootstrap: ensure the project root is on sys.path ────────────────────────
# This allows running the script from the project root without installing
# the package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Force production .env loading ────────────────────────────────────────────
# Unset test-mode markers so get_settings() always reads .env, not .env.test.
# This script must run against real credentials, never test dummies.
import os as _os
_os.environ.pop("APP_ENV", None)
_os.environ.pop("PYTEST_CURRENT_TEST", None)

from app.config import get_settings

# Clear any previously cached settings (e.g. from .env.test loaded by pytest).
get_settings.cache_clear()

from app.core.audio import (
    encode_gemini_to_twilio,
    mulaw_bytes_to_pcm,
    pcm_bytes_to_wav,
    GEMINI_OUTPUT_SAMPLE_RATE,
    TWILIO_SAMPLE_RATE,
)
from app.core.gemini_session import GeminiVoiceSession

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gemini_live_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Test configuration ────────────────────────────────────────────────────────
# System instruction for the test: only vocal style, no character or scenario.
TEST_SYSTEM_INSTRUCTION = (
    "Habla exclusivamente en español de España con una voz adulta, "
    "natural, sobria y profesional. "
    "Responde únicamente con la frase indicada, sin añadir nada más."
)

# Sentence that Gemini must say — short enough to complete in one turn.
TEST_PHRASE = "Buenos días. Esta es una prueba de voz para Boston Medical."

# Prompt sent as the user turn.
TEST_TURN_TEXT = f'Di exactamente: "{TEST_PHRASE}"'

# Maximum time to wait for audio after sending the text turn (seconds).
AUDIO_TIMEOUT_SECONDS = 30


# ── Helpers ───────────────────────────────────────────────────────────────────

def _separator(char: str = "─", width: int = 60) -> str:
    return char * width


def _print_header() -> None:
    print(_separator("═"))
    print("  Gemini Live Integration Test — bm-seleccion-gemini")
    print(_separator("═"))


def _print_section(title: str) -> None:
    print(f"\n{_separator()}")
    print(f"  {title}")
    print(_separator())


def _safe_model_info(settings) -> None:
    """Print connection parameters without exposing the API key."""
    print(f"  Model  : {settings.gemini_model}")
    print(f"  Voice  : {settings.gemini_voice_name}")
    print(f"  Thinking: {settings.gemini_thinking_level}")
    print(f"  VAD silence : {settings.vad_silence_duration_ms} ms")
    print(f"  VAD prefix  : {settings.vad_prefix_padding_ms} ms")
    print(f"  API key : {'✓ configured' if settings.gemini_api_key else '✗ MISSING'}")


def _write_wav(path: Path, wav_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)
    size_kb = len(wav_bytes) / 1024
    logger.info("WAV written: %s (%.1f KB)", path.relative_to(PROJECT_ROOT), size_kb)


def _wav_duration_seconds(wav_bytes: bytes, sample_rate: int) -> float:
    """Estimate WAV duration from byte count (16-bit mono)."""
    # RIFF header is 44 bytes; data section is the rest.
    data_bytes = max(0, len(wav_bytes) - 44)
    samples = data_bytes // 2  # 16-bit = 2 bytes per sample
    return samples / sample_rate if sample_rate else 0.0


# ── Main test coroutine ───────────────────────────────────────────────────────

async def run_test() -> int:
    """Execute the Gemini Live integration test.

    Returns:
        0 on success, 1 on failure.
    """
    _print_header()

    # ── 1. Load and validate configuration ───────────────────────────────────
    _print_section("1. Configuration")
    settings = get_settings()
    _safe_model_info(settings)

    if not settings.gemini_api_key:
        print("\n  ✗ GEMINI_API_KEY is not set.")
        print("    Set it in your .env file and re-run this script.")
        return 1

    # ── 2. Open Gemini Live session ───────────────────────────────────────────
    _print_section("2. Connecting to Gemini Live")
    session = GeminiVoiceSession(
        settings=settings,
        system_instruction=TEST_SYSTEM_INSTRUCTION,
    )

    t_connect_start = time.monotonic()
    try:
        await session.connect()
    except ValueError as exc:
        print(f"\n  ✗ Configuration error: {exc}")
        return 1
    except Exception as exc:
        print(f"\n  ✗ Connection failed: {type(exc).__name__}: {exc}")
        return 1

    t_connected = time.monotonic()
    print(f"  ✓ Connected in {t_connected - t_connect_start:.2f}s")

    # ── 3. Collect events with a global timeout ───────────────────────────────
    _print_section("3. Sending text turn and collecting audio")

    setup_received = False
    audio_chunks_raw: list[bytes] = []       # PCM 24 kHz bytes per chunk
    mulaw_chunks: list[bytes] = []           # µ-law 8 kHz bytes per chunk
    turn_complete_received = False
    unknown_events: list[dict] = []
    gemini_rate_state = None
    t_first_audio: float | None = None
    t_turn_complete: float | None = None

    print(f"  Phrase to request: «{TEST_PHRASE}»")
    print(f"  Timeout: {AUDIO_TIMEOUT_SECONDS}s")

    try:
        async with asyncio.timeout(AUDIO_TIMEOUT_SECONDS):
            async for event in session.receive():
                etype = event["type"]

                if etype == "setup_complete":
                    setup_received = True
                    t_setup = time.monotonic()
                    print(f"  ✓ setupComplete received ({t_setup - t_connected:.2f}s after connect)")

                    # Send the text turn immediately after setup
                    await session.send_text_turn(TEST_TURN_TEXT)
                    print(f"  → Text turn sent: {TEST_TURN_TEXT!r}")

                elif etype == "audio":
                    raw_b64 = event["data"]

                    # Decode PCM 24 kHz chunk
                    pcm_chunk = base64.b64decode(raw_b64)
                    audio_chunks_raw.append(pcm_chunk)

                    if t_first_audio is None:
                        t_first_audio = time.monotonic()
                        print(f"  ✓ First audio chunk received ({t_first_audio - t_connected:.2f}s after connect)")

                    # Convert to µ-law 8 kHz via the streaming converter
                    mulaw_b64, gemini_rate_state = encode_gemini_to_twilio(
                        raw_b64, gemini_rate_state
                    )
                    if mulaw_b64:
                        mulaw_chunks.append(base64.b64decode(mulaw_b64))

                elif etype == "turn_complete":
                    turn_complete_received = True
                    t_turn_complete = time.monotonic()
                    print(f"  ✓ turnComplete received ({t_turn_complete - t_connected:.2f}s after connect)")
                    break  # All audio collected

                elif etype == "interrupted":
                    print("  ⚠ Interrupted signal received (unexpected in text-only test)")

                elif etype == "unknown":
                    unknown_events.append(event.get("raw", {}))
                    logger.debug("Unknown Gemini event: %s", event.get("raw"))

    except TimeoutError:
        print(f"\n  ✗ Timeout: no turn_complete received within {AUDIO_TIMEOUT_SECONDS}s")
        print(f"    Audio chunks collected before timeout: {len(audio_chunks_raw)}")
        if not audio_chunks_raw:
            await session.close()
            return 1
        # Still try to save whatever audio arrived

    except Exception as exc:
        print(f"\n  ✗ Error during event loop: {type(exc).__name__}: {exc}")
        await session.close()
        return 1

    # ── 4. Close session ──────────────────────────────────────────────────────
    _print_section("4. Closing session")
    await session.close()
    print("  ✓ Session closed cleanly")

    # ── 5. Validate audio received ────────────────────────────────────────────
    _print_section("5. Audio validation")

    if not audio_chunks_raw:
        print("  ✗ No audio received from Gemini.")
        return 1

    total_pcm_bytes = b"".join(audio_chunks_raw)
    total_mulaw_bytes = b"".join(mulaw_chunks)
    total_samples_24k = len(total_pcm_bytes) // 2
    duration_24k = total_samples_24k / GEMINI_OUTPUT_SAMPLE_RATE

    print(f"  Audio chunks received  : {len(audio_chunks_raw)}")
    print(f"  PCM 24 kHz total bytes : {len(total_pcm_bytes):,}")
    print(f"  µ-law 8 kHz total bytes: {len(total_mulaw_bytes):,}")
    print(f"  Estimated duration     : {duration_24k:.2f}s")

    # ── 6. Write WAV files ────────────────────────────────────────────────────
    _print_section("6. Writing WAV files")

    # File 1: Gemini raw output — PCM 24 kHz
    wav_24k = pcm_bytes_to_wav(total_pcm_bytes, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
    path_24k = OUTPUT_DIR / "gemini_output_24khz.wav"
    _write_wav(path_24k, wav_24k)
    dur_24k = _wav_duration_seconds(wav_24k, GEMINI_OUTPUT_SAMPLE_RATE)

    # File 2: Twilio pipeline simulation — µ-law decoded back to PCM 8 kHz
    # (µ-law itself cannot be played as standard WAV without a codec;
    #  decoding back to PCM makes it universally playable)
    pcm_8k_bytes = mulaw_bytes_to_pcm(total_mulaw_bytes)
    wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
    path_8k = OUTPUT_DIR / "twilio_output_8khz.wav"
    _write_wav(path_8k, wav_8k)
    dur_8k = _wav_duration_seconds(wav_8k, TWILIO_SAMPLE_RATE)

    # ── 7. Summary ────────────────────────────────────────────────────────────
    _print_section("7. Summary")

    ok = (
        setup_received
        and bool(audio_chunks_raw)
        and turn_complete_received
    )

    print(f"  setupComplete received : {'✓' if setup_received else '✗'}")
    print(f"  Audio received         : {'✓' if audio_chunks_raw else '✗'}")
    print(f"  turnComplete received  : {'✓' if turn_complete_received else '✗'}")
    print()
    print(f"  gemini_output_24khz.wav : {len(wav_24k):,} bytes | {dur_24k:.2f}s | 24 kHz mono 16-bit PCM")
    print(f"  twilio_output_8khz.wav  : {len(wav_8k):,} bytes | {dur_8k:.2f}s |  8 kHz mono 16-bit PCM")
    print()

    if unknown_events:
        print(f"  ⚠ Unknown events from Gemini ({len(unknown_events)}):")
        for ev in unknown_events[:5]:
            print(f"    {ev}")

    if ok:
        print("  ✓ ALL CHECKS PASSED")
        print()
        print(f"  Listen to the files to confirm the phrase was spoken:")
        print(f"    {path_24k}")
        print(f"    {path_8k}")
        print()
        print("  Expected phrase:")
        print(f"    «{TEST_PHRASE}»")
        print()
        print("  Expected voice: Algieba (español de España, adulta, sobria)")
    else:
        print("  ✗ SOME CHECKS FAILED — review the output above")

    print(_separator("═"))
    return 0 if ok else 1


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = asyncio.run(run_test())
    sys.exit(exit_code)
