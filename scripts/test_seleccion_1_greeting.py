"""Integration test script for scenario 'seleccion_1' greeting.

This script validates that:
  - Scenario 'seleccion_1' loads correctly from the registry.
  - The composed system instruction (vocal rules + markdown prompt) is loaded.
  - A real Gemini Live session is opened.
  - The backend requests the exact 'initial_message' after setupComplete.
  - The spoken audio response is received from Gemini.
  - The turnComplete signal is received.
  - Spoken audio is converted and written to:
    - tmp/audio-tests/seleccion_1_greeting_24khz.wav (Gemini raw output)
    - tmp/audio-tests/seleccion_1_greeting_8khz.wav (Twilio pipeline simulation)
  - The session is terminated cleanly.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_seleccion_1_greeting.py
"""
import asyncio
import base64
import logging
import sys
import time
from pathlib import Path

# ── Windows console: force UTF-8 output ──────────────────────────────────────
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Bootstrap: ensure the project root is on sys.path ────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Force production .env loading ────────────────────────────────────────────
import os as _os
_os.environ.pop("APP_ENV", None)
_os.environ.pop("PYTEST_CURRENT_TEST", None)

from app.config import get_settings
get_settings.cache_clear()

from app.core.audio import (
    encode_gemini_to_twilio,
    mulaw_bytes_to_pcm,
    pcm_bytes_to_wav,
    GEMINI_OUTPUT_SAMPLE_RATE,
    TWILIO_SAMPLE_RATE,
)
from app.core.gemini_session import GeminiVoiceSession
from app.scenarios.registry import get_scenario

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seleccion_1_greeting_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Timeout configuration (seconds) ──────────────────────────────────────────
AUDIO_TIMEOUT_SECONDS = 30


# ── Helpers ───────────────────────────────────────────────────────────────────

def _separator(char: str = "─", width: int = 60) -> str:
    return char * width


def _print_header() -> None:
    print(_separator("═"))
    print("  Scenario 'seleccion_1' Greeting Test — bm-seleccion-gemini")
    print(_separator("═"))


def _print_section(title: str) -> None:
    print(f"\n{_separator()}")
    print(f"  {title}")
    print(_separator())


def _safe_scenario_info(scenario) -> None:
    print(f"  Scenario ID   : {scenario.scenario_id}")
    print(f"  Display Name  : {scenario.display_name}")
    print(f"  External ID   : {scenario.external_scenario_id}")
    print(f"  Required Tools: {scenario.required_tools}")
    print(f"  Initial Msg   : «{scenario.initial_message}»")
    print(f"  Prompt length : {len(scenario.system_instruction)} characters")


def _write_wav(path: Path, wav_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes)
    size_kb = len(wav_bytes) / 1024
    logger.info("WAV written: %s (%.1f KB)", path.relative_to(PROJECT_ROOT), size_kb)


def _wav_duration_seconds(wav_bytes: bytes, sample_rate: int) -> float:
    data_bytes = max(0, len(wav_bytes) - 44)
    samples = data_bytes // 2
    return samples / sample_rate if sample_rate else 0.0


# ── Main test coroutine ───────────────────────────────────────────────────────

async def run_test() -> int:
    _print_header()

    # ── 1. Load and validate scenario ─────────────────────────────────────────
    _print_section("1. Loading Scenario Configuration")
    scenario = get_scenario("seleccion_1")
    if not scenario:
        print("\n  ✗ Scenario 'seleccion_1' could not be resolved from registry.")
        return 1
    _safe_scenario_info(scenario)

    settings = get_settings()
    if not settings.gemini_api_key:
        print("\n  ✗ GEMINI_API_KEY is not set.")
        print("    Set it in your .env file and re-run this script.")
        return 1

    # ── 2. Open Gemini Live session ───────────────────────────────────────────
    _print_section("2. Connecting to Gemini Live")
    session = GeminiVoiceSession(
        settings=settings,
        system_instruction=scenario.system_instruction,
    )

    t_connect_start = time.monotonic()
    try:
        await session.connect()
    except Exception as exc:
        print(f"\n  ✗ Connection failed: {type(exc).__name__}: {exc}")
        return 1

    t_connected = time.monotonic()
    print(f"  ✓ Connected in {t_connected - t_connect_start:.2f}s")

    # ── 3. Collect events and greeting audio ──────────────────────────────────
    _print_section("3. Waiting for setupComplete & requesting greeting")

    setup_received = False
    audio_chunks_raw: list[bytes] = []
    mulaw_chunks: list[bytes] = []
    turn_complete_received = False
    gemini_rate_state = None
    t_first_audio: float | None = None
    t_turn_complete: float | None = None

    try:
        async with asyncio.timeout(AUDIO_TIMEOUT_SECONDS):
            async for event in session.receive():
                etype = event["type"]

                if etype == "setup_complete":
                    setup_received = True
                    print(f"  ✓ setupComplete received")
                    
                    # Request the initial message turn exactly as specified
                    turn_text = f'Di únicamente, palabra por palabra, sin preámbulos ni comentarios adicionales: "{scenario.initial_message}"'
                    await session.send_text_turn(turn_text)
                    print(f"  → Requested greeting: {scenario.initial_message!r}")

                elif etype == "audio":
                    raw_b64 = event["data"]
                    pcm_chunk = base64.b64decode(raw_b64)
                    audio_chunks_raw.append(pcm_chunk)

                    if t_first_audio is None:
                        t_first_audio = time.monotonic()
                        print(f"  ✓ Spoken audio stream started")

                    # Twilio pipeline simulation
                    mulaw_b64, gemini_rate_state = encode_gemini_to_twilio(
                        raw_b64, gemini_rate_state
                    )
                    if mulaw_b64:
                        mulaw_chunks.append(base64.b64decode(mulaw_b64))

                elif etype == "turn_complete":
                    turn_complete_received = True
                    t_turn_complete = time.monotonic()
                    print(f"  ✓ turnComplete received (greeting complete)")
                    break

    except TimeoutError:
        print(f"\n  ✗ Timeout: no turnComplete received within {AUDIO_TIMEOUT_SECONDS}s")
        if not audio_chunks_raw:
            await session.close()
            return 1

    except Exception as exc:
        print(f"\n  ✗ Error: {type(exc).__name__}: {exc}")
        await session.close()
        return 1

    # ── 4. Close session ──────────────────────────────────────────────────────
    _print_section("4. Closing Session")
    await session.close()
    print("  ✓ Closed cleanly")

    # ── 5. Validate audio ─────────────────────────────────────────────────────
    _print_section("5. Audio Validation")
    total_pcm_bytes = b"".join(audio_chunks_raw)
    total_mulaw_bytes = b"".join(mulaw_chunks)
    duration = (len(total_pcm_bytes) // 2) / GEMINI_OUTPUT_SAMPLE_RATE

    print(f"  Audio chunks received  : {len(audio_chunks_raw)}")
    print(f"  PCM 24 kHz total bytes : {len(total_pcm_bytes):,}")
    print(f"  µ-law 8 kHz total bytes: {len(total_mulaw_bytes):,}")
    print(f"  Duration               : {duration:.2f}s")

    # ── 6. Write WAV files ────────────────────────────────────────────────────
    _print_section("6. Generating output files")
    
    wav_24k = pcm_bytes_to_wav(total_pcm_bytes, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
    path_24k = OUTPUT_DIR / "seleccion_1_greeting_24khz.wav"
    _write_wav(path_24k, wav_24k)

    pcm_8k_bytes = mulaw_bytes_to_pcm(total_mulaw_bytes)
    wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
    path_8k = OUTPUT_DIR / "seleccion_1_greeting_8khz.wav"
    _write_wav(path_8k, wav_8k)

    # ── 7. Final checks ───────────────────────────────────────────────────────
    _print_section("7. Summary")
    success = setup_received and bool(audio_chunks_raw) and turn_complete_received
    
    print(f"  setupComplete received : {'✓' if setup_received else '✗'}")
    print(f"  Audio received         : {'✓' if audio_chunks_raw else '✗'}")
    print(f"  turnComplete received  : {'✓' if turn_complete_received else '✗'}")
    print()
    print(f"  seleccion_1_greeting_24khz.wav : {len(wav_24k):,} bytes | {duration:.2f}s | 24 kHz mono PCM")
    print(f"  seleccion_1_greeting_8khz.wav  : {len(wav_8k):,} bytes | {duration:.2f}s |  8 kHz mono PCM")
    print()
    
    if success:
        print("  ✓ ALL INTEGRATION CHECKS PASSED")
        print("    Verify spoken files:")
        print(f"      {path_24k}")
        print(f"      {path_8k}")
    else:
        print("  ✗ SOME INTEGRATION CHECKS FAILED")
        
    print(_separator("═"))
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_test())
    sys.exit(exit_code)
