"""Integration test script to validate the end-to-end dialog and closure of scenario 'seleccion_2'.

Runs a single conversation from start to finish, checking that the session advances through
onboarding, saves candidate context, begins the roleplay, handles the shipment delay complaint,
accepts the candidate's incidence registry procedure, and closes with the exact termination phrase.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_seleccion_2_end_to_end.py
"""
import asyncio
import base64
import logging
import sys
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
from app.scenarios.registry import get_scenario, get_scenario_handlers

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seleccion_2_end_to_end_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Timeout configuration (seconds) ──────────────────────────────────────────
GLOBAL_TIMEOUT_SECONDS = 300

ROLEPLAY_INPUTS = [
    "Buenos días. Antes de nada, ¿me puede indicar su nombre completo?",
    "Entiendo su malestar. Para poder localizar su ficha y dejar constancia correctamente, ¿me indica su nombre completo?",
    "Gracias, Miguel. Entiendo que ya comunicó una incidencia con el envío y que continúa sin resolverse. Comprendo que esta situación le genere desconfianza.",
    "No puedo confirmarle dónde está el paquete ni darle una fecha de entrega que no tenga verificada, pero sí puedo dejar constancia formal de que la incidencia continúa.",
    "También voy a dejar registrado que esta situación le está haciendo plantearse pausar el tratamiento y solicitar la devolución.",
    "Entiendo que esté muy molesto. No quiero darle información que no pueda garantizar. Mi intención es que quede recogido todo lo ocurrido y la repercusión que está teniendo para usted.",
    "¿Hay alguna otra cuestión que quiera que deje expresamente anotada?"
]


async def run_execution() -> tuple[bool, list[str], str, bytes, bytes, int, OnboardingPhase]:
    """Run a single end-to-end conversation for seleccion_2.

    Returns:
        tuple (success_status, transcript_lines, failure_reason, pcm_24k, mulaw_8k, tool_calls, final_phase)
    """
    scenario = get_scenario("seleccion_2")
    if not scenario:
        return False, ["Scenario seleccion_2 not found"], "Scenario not found", b"", b"", 0, OnboardingPhase.WAITING_READY

    settings = get_settings()
    session = GeminiVoiceSession(
        settings=settings,
        system_instruction=scenario.system_instruction,
        tools=scenario.tools,
        enable_transcription=True,
        roleplay_transition_phrase=scenario.roleplay_transition_phrase,
        roleplay_initial_phrase=scenario.roleplay_initial_phrase,
        completion_phrase=scenario.completion_phrase,
    )

    tool_call_count = 0
    real_handlers = get_scenario_handlers("seleccion_2", session)
    real_save_handler = real_handlers["save_candidate_context"]

    def trace_save_handler(args):
        nonlocal tool_call_count
        tool_call_count += 1
        return real_save_handler(args)

    session.register_tool_handler("save_candidate_context", trace_save_handler)

    audio_chunks_raw: list[bytes] = []
    mulaw_chunks: list[bytes] = []
    gemini_rate_state = None

    transcript_lines: list[str] = []
    current_model_text = []

    # Active roleplay tracking variables
    roleplay_turn_index = 0
    final_closing_sent = False
    
    # State verification variables
    initial_context_ref = None
    success = True
    failure_reason = ""

    try:
        await session.connect()
        async with asyncio.timeout(GLOBAL_TIMEOUT_SECONDS):
            async for event in session.receive():
                etype = event["type"]

                if etype == "setup_complete":
                    turn_text = f'Di únicamente, palabra por palabra, sin preámbulos ni comentarios adicionales: "{scenario.initial_message}"'
                    await session.send_text_turn(turn_text)
                    transcript_lines.append(f"SYS_INIT: {scenario.initial_message}")

                elif etype == "text":
                    current_model_text.append(event["data"])

                elif etype == "audio":
                    raw_b64 = event["data"]
                    pcm_chunk = base64.b64decode(raw_b64)
                    audio_chunks_raw.append(pcm_chunk)

                    mulaw_b64, gemini_rate_state = encode_gemini_to_twilio(raw_b64, gemini_rate_state)
                    if mulaw_b64:
                        mulaw_chunks.append(base64.b64decode(mulaw_b64))

                elif etype == "turn_complete":
                    model_speech = "".join(current_model_text).strip()
                    current_model_text = []
                    transcript_lines.append(f"GEMINI: {model_speech}")

                    # Active roleplay evaluation
                    if session.onboarding_phase in [OnboardingPhase.ROLEPLAY_ACTIVE, OnboardingPhase.ROLEPLAY_FINISHED]:
                        if roleplay_turn_index > 0:
                            # 1. Capture initial reference context
                            if initial_context_ref is None:
                                initial_context_ref = dict(session.candidate_context)

                            # 2. Immutability & Tool execution validations
                            if session.candidate_context != initial_context_ref:
                                success = False
                                failure_reason = f"Candidate context was modified during roleplay: {session.candidate_context}"
                                break
                            if session.candidate_context.get("saved") is not True:
                                success = False
                                failure_reason = f"Candidate context saved flag is not True"
                                break
                            if tool_call_count != 1:
                                success = False
                                failure_reason = f"Tool executed {tool_call_count} times instead of exactly 1"
                                break

                        # Exit loop if session transitioned to finished
                        if session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED:
                            break

                        # Limit check to prevent infinite loops
                        if roleplay_turn_index >= 12:
                            success = False
                            failure_reason = "Exceeded the maximum limit of 12 candidate roleplay turns."
                            break

                    # ── Stepper Logic ──
                    if session.onboarding_phase not in [OnboardingPhase.ROLEPLAY_ACTIVE, OnboardingPhase.ROLEPLAY_FINISHED]:
                        # Onboarding Stepper
                        candidate_msg = None
                        if session.onboarding_phase == OnboardingPhase.WAITING_READY:
                            candidate_msg = "Sí, estoy preparado."
                        elif session.onboarding_phase == OnboardingPhase.WAITING_CANDIDATE_DATA:
                            candidate_msg = "Daniel Martínez."
                        elif session.onboarding_phase == OnboardingPhase.WAITING_DATA_CONFIRMATION:
                            candidate_msg = "Sí, es correcto."
                        elif session.onboarding_phase == OnboardingPhase.READY_TO_ASK_RGPD:
                            if "correcto" in model_speech.lower() or "martinez" in model_speech.lower() or "confirm" in model_speech.lower():
                                candidate_msg = "Sí, es correcto."
                        elif session.onboarding_phase == OnboardingPhase.WAITING_RGPD_ACCEPTANCE:
                            candidate_msg = "Sí, acepto ambas cosas."
                        elif session.onboarding_phase == OnboardingPhase.READY_TO_SAVE:
                            if "rgpd" in model_speech.lower() or "aceptas" in model_speech.lower():
                                candidate_msg = "Sí, acepto ambas cosas."
                        elif session.onboarding_phase == OnboardingPhase.EXPLANATION:
                            candidate_msg = "No tengo dudas, podemos comenzar."

                        if candidate_msg:
                            transcript_lines.append(f"USER: {candidate_msg}")
                            await session.send_text_turn(candidate_msg)
                    else:
                        # Roleplay Stepper
                        candidate_msg = None
                        if roleplay_turn_index < len(ROLEPLAY_INPUTS):
                            candidate_msg = ROLEPLAY_INPUTS[roleplay_turn_index]
                        elif not final_closing_sent:
                            # Send final closure response after checking if no other concerns
                            candidate_msg = "De acuerdo, Miguel. Queda registrada la incidencia y todo lo que nos ha trasladado. Gracias por llamar a Boston Medical."
                            final_closing_sent = True
                        else:
                            # Waiting for model to state "La prueba ha terminado. Gracias por participar."
                            pass

                        if candidate_msg:
                            roleplay_turn_index += 1
                            transcript_lines.append(f"USER: {candidate_msg}")
                            await session.send_text_turn(candidate_msg)

    except Exception as exc:
        success = False
        failure_reason = f"Exception: {type(exc).__name__}: {exc}"
    finally:
        await session.close()

    # Final post-loop validations
    if success:
        if session.onboarding_phase != OnboardingPhase.ROLEPLAY_FINISHED:
            success = False
            failure_reason = f"Did not reach ROLEPLAY_FINISHED state. Final phase: {session.onboarding_phase}"

    if not success:
        transcript_lines.append(f"  [VAL_FAIL] Reason: {failure_reason}")

    pcm_24k = b"".join(audio_chunks_raw)
    mulaw_8k = b"".join(mulaw_chunks)

    return success, transcript_lines, failure_reason, pcm_24k, mulaw_8k, tool_call_count, session.onboarding_phase


async def main() -> int:
    print("=============================================================", flush=True)
    print("  Scenario 'seleccion_2' End-to-End Dialog & Cierre Run", flush=True)
    print("=============================================================\n", flush=True)

    success, lines, fail_reason, pcm, mulaw, tool_calls, final_phase = await run_execution()

    print("\n".join(lines), flush=True)
    print("\n=============================================================", flush=True)
    print(f"  RUN STATUS: {'PASS' if success else 'FAIL'}", flush=True)
    print("=============================================================", flush=True)
    print(f"  Tool calls: {tool_calls}", flush=True)
    print(f"  Final session phase: {final_phase}", flush=True)
    if not success:
        print(f"  Failure reason: {fail_reason}", flush=True)
    print("=============================================================\n", flush=True)

    # Save transcript file
    path_txt = OUTPUT_DIR / "seleccion_2_end_to_end.txt"
    path_txt.parent.mkdir(parents=True, exist_ok=True)
    path_txt.write_text("\n".join(lines), encoding="utf-8")

    # Save audio files
    if len(pcm) > 0:
        wav_24k = pcm_bytes_to_wav(pcm, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
        path_24k = OUTPUT_DIR / "seleccion_2_end_to_end_24khz.wav"
        path_24k.write_bytes(wav_24k)
        
        pcm_8k_bytes = mulaw_bytes_to_pcm(mulaw)
        wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
        path_8k = OUTPUT_DIR / "seleccion_2_end_to_end_8khz.wav"
        path_8k.write_bytes(wav_8k)

    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
