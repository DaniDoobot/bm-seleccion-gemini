"""Roleplay simulation start integration test script for scenario 'seleccion_1'.

Simulates the complete onboarding sequence and transitions into the active roleplay
once the candidate confirms they have no doubts. Verifies all transitions and phrases.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_seleccion_1_roleplay_start.py
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
from app.core.gemini_session import GeminiVoiceSession, OnboardingPhase
from app.scenarios.registry import get_scenario
from app.scenarios.seleccion_1 import get_seleccion_1_handlers

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING, # keeps stdout output extremely clean
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seleccion_1_roleplay_start_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Timeout configuration (seconds) ──────────────────────────────────────────
GLOBAL_TIMEOUT_SECONDS = 120

# ── Candidate inputs simulation sequence ──────────────────────────────────────
CANDIDATE_RESPONSES = [
    "Sí, estoy preparado.",
    "Daniel Martínez.",
    "Sí, es correcto.",
    "Sí, acepto ambas cosas.",
    "No tengo dudas, podemos comenzar."  # Triggers the roleplay start!
]


async def run_test() -> int:
    # Load scenario and settings
    scenario = get_scenario("seleccion_1")
    if not scenario:
        print("\n  ✗ Scenario 'seleccion_1' could not be resolved.")
        return 1

    settings = get_settings()
    if not settings.gemini_api_key:
        print("\n  ✗ GEMINI_API_KEY is not set.")
        return 1

    # Instantiate the session requesting both AUDIO and TEXT modalities
    session = GeminiVoiceSession(
        settings=settings,
        system_instruction=scenario.system_instruction,
        tools=scenario.tools,
        enable_transcription=True,
    )

    # Track tool calls and duplicates
    tool_call_count = 0
    tool_call_args = None
    
    # Wrap scenario handler to trace execution count and args
    real_handlers = get_seleccion_1_handlers(session)
    real_save_handler = real_handlers["save_candidate_context"]

    def trace_save_handler(args):
        nonlocal tool_call_count, tool_call_args
        print("[TOOL CALL RECEIVED]")
        print(f"[PHASE BEFORE TOOL] {session.onboarding_phase.value.upper()}")
        tool_call_count += 1
        tool_call_args = args
        res = real_save_handler(args)
        success_str = "true" if res.get("success") else "false"
        print(f"[TOOL RESULT] success={success_str}")
        print(f"[PHASE AFTER TOOL] {session.onboarding_phase.value.upper()}")
        print() # spacing
        return res

    session.register_tool_handler("save_candidate_context", trace_save_handler)

    # Trackers
    audio_chunks_raw: list[bytes] = []
    mulaw_chunks: list[bytes] = []
    gemini_rate_state = None

    transcript_lines: list[str] = []
    current_model_text = []

    turn_index = 0
    setup_complete = False
    last_known_phase = session.onboarding_phase.value.upper()

    t_connect_start = time.monotonic()
    try:
        await session.connect()
        t_connected = time.monotonic()

        async with asyncio.timeout(GLOBAL_TIMEOUT_SECONDS):
            async for event in session.receive():
                etype = event["type"]

                if etype == "setup_complete":
                    setup_complete = True
                    # Trigger scenario greeting
                    turn_text = f'Di únicamente, palabra por palabra, sin preámbulos ni comentarios adicionales: "{scenario.initial_message}"'
                    await session.send_text_turn(turn_text)
                    transcript_lines.append(f"SYS_INIT: {scenario.initial_message}")
                    last_known_phase = session.onboarding_phase.value.upper()

                elif etype == "text":
                    # Collect model speech transcript
                    text_part = event["data"]
                    current_model_text.append(text_part)

                elif etype == "audio":
                    # Save raw audio
                    raw_b64 = event["data"]
                    pcm_chunk = base64.b64decode(raw_b64)
                    audio_chunks_raw.append(pcm_chunk)

                    # Encode to Twilio format
                    mulaw_b64, gemini_rate_state = encode_gemini_to_twilio(
                        raw_b64, gemini_rate_state
                    )
                    if mulaw_b64:
                        mulaw_chunks.append(base64.b64decode(mulaw_b64))

                elif etype == "turn_complete":
                    model_speech = "".join(current_model_text).strip()
                    current_model_text = []
                    transcript_lines.append(f"GEMINI: {model_speech}")
                    
                    # Print sequential model turn completed logs
                    print(f"[MODEL TRANSCRIPT COMPLETE] {model_speech}")
                    print(f"[PHASE BEFORE MODEL TURN_COMPLETE] {last_known_phase}")
                    print(f"[MODEL TURN_COMPLETE]")
                    print(f"[PHASE AFTER MODEL TURN_COMPLETE] {session.onboarding_phase.value.upper()}")
                    print()
                    
                    model_speech_lower = model_speech.lower()
                    
                    # 1. Did Gemini ask for RGPD or mention recording during the name confirmation turn?
                    if turn_index == 2:
                        if any(k in model_speech_lower for k in ["rgpd", "grabacion", "grabación", "aceptas", "aceptación", "aceptacion"]):
                            print("\n  ✗ FAIL: Gemini combined name confirmation and RGPD question in Turn 2 response!")
                            return 1

                    # 2. Did Gemini start the roleplay prematurely?
                    if turn_index < 5:
                        if any(k in model_speech for k in [
                            "Perfecto, comenzamos la simulación.",
                            "A partir de ahora soy el paciente.",
                            "Mira, quiero hablar con el doctor ahora mismo."
                        ]):
                            print("\n  ✗ FAIL: Gemini started the roleplay prematurely!")
                            return 1

                    # 3. Tool call execution count checks
                    if tool_call_count > 1:
                        print(f"\n  ✗ FAIL: save_candidate_context tool executed {tool_call_count} times! (Only 1 allowed)")
                        return 1
                    
                    if tool_call_count > 0 and turn_index < 4:
                        print(f"\n  ✗ FAIL: save_candidate_context executed prematurely before Turn 4!")
                        return 1

                    # Exit condition: Roleplay is fully active
                    if session.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE:
                        break

                    # Send next candidate text response if available
                    if turn_index < len(CANDIDATE_RESPONSES):
                        candidate_msg = CANDIDATE_RESPONSES[turn_index]
                        turn_index += 1
                        
                        # Print user sent logs
                        print(f"[PHASE BEFORE USER SEND] {session.onboarding_phase.value.upper()}")
                        print(f"[USER SENT] {candidate_msg}")
                        await session.send_text_turn(candidate_msg)
                        print(f"[PHASE AFTER USER SEND] {session.onboarding_phase.value.upper()}")
                        print()
                        
                        last_known_phase = session.onboarding_phase.value.upper()
                    else:
                        print("\n  [SYSTEM] Sim inputs exhausted but roleplay not started. Stopping.")
                        break

                # Update tracking of phase changes inside loop
                last_known_phase = session.onboarding_phase.value.upper()

    except TimeoutError:
        print(f"\n  ✗ Timeout: global test limit of {GLOBAL_TIMEOUT_SECONDS}s reached.")
        return 1
    except Exception as exc:
        print(f"\n  ✗ Error: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await session.close()

    # ── Verify test rules ─────────────────────────────────────────────────────
    if tool_call_count != 1:
        print(f"\n  ✗ FAIL: save_candidate_context was not executed exactly once. Count: {tool_call_count}")
        return 1
    
    if session.onboarding_phase != OnboardingPhase.ROLEPLAY_ACTIVE:
        print(f"\n  ✗ FAIL: Did not reach ROLEPLAY_ACTIVE phase. Final phase: {session.onboarding_phase}")
        return 1

    # ── Write output files ────────────────────────────────────────────────────
    # 1. 24kHz WAV
    total_pcm_bytes = b"".join(audio_chunks_raw)
    wav_24k = pcm_bytes_to_wav(total_pcm_bytes, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
    path_24k = OUTPUT_DIR / "seleccion_1_roleplay_start_24khz.wav"
    path_24k.parent.mkdir(parents=True, exist_ok=True)
    path_24k.write_bytes(wav_24k)

    # 2. 8kHz WAV
    total_mulaw_bytes = b"".join(mulaw_chunks)
    pcm_8k_bytes = mulaw_bytes_to_pcm(total_mulaw_bytes)
    wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
    path_8k = OUTPUT_DIR / "seleccion_1_roleplay_start_8khz.wav"
    path_8k.write_bytes(wav_8k)

    # 3. Transcript text file
    path_txt = OUTPUT_DIR / "seleccion_1_roleplay_start.txt"
    path_txt.write_text("\n".join(transcript_lines), encoding="utf-8")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_test())
    sys.exit(exit_code)
