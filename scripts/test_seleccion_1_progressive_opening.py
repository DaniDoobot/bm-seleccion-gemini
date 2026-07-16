"""Integration test script to validate the progressive opening sequence of scenario 'seleccion_1'.

Runs five independent sequential executions, verifying that the patient resists initially
and opens up progressively until full disclosure in Turn 6.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_seleccion_1_progressive_opening.py
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
from tests.roleplay_validators import classify_response

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seleccion_1_progressive_opening_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Timeout configuration (seconds) ──────────────────────────────────────────
GLOBAL_TIMEOUT_SECONDS = 180

ROLEPLAY_INPUTS = [
    "Buenos días. Antes de nada, ¿me puede indicar su nombre completo?",
    "Entiendo que quiera hablar directamente con el doctor. Para intentar ayudarle y no hacerle perder más tiempo, necesito conocer al menos de forma general qué ocurre.",
    "Comprendo que pueda resultarle incómodo. No necesito que entre en detalles si no quiere, pero saber el tipo de consulta me permitirá indicarle cómo podemos gestionarlo.",
    "No quiero invadir su intimidad. Mi intención es comprobar si puedo ayudarle directamente o dejar correctamente recogido lo que necesitan para evitarle más vueltas.",
    "Entiendo que preferiría hablar con el doctor. Puede explicármelo solo por encima; no necesita darme detalles que no quiera compartir.",
    "Gracias. Dígame únicamente cuál es la cuestión general y así podré valorar si podemos ayudarle desde atención al paciente o si corresponde dejar nota.",
    "Puede decírmelo con sus propias palabras. Le escucho y trataré la información con discreción."
]


def check_name_identity(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    return "miguel" in text_clean and "perez" in text_clean and "gomez" in text_clean


async def run_single_execution(exec_num: int) -> tuple[bool, list[str], dict[int, str], int, int, str, bytes, bytes]:
    """Run a single onboarding and 6-turn progressive roleplay execution.

    Returns:
        tuple (success_status, transcript_lines, classifications, partial_turn, full_turn, naturalness, pcm_24k, mulaw_8k)
    """
    scenario = get_scenario("seleccion_1")
    if not scenario:
        return False, ["Scenario seleccion_1 not found"], {}, 0, 0, "No run", b"", b""

    settings = get_settings()
    session = GeminiVoiceSession(
        settings=settings,
        system_instruction=scenario.system_instruction,
        tools=scenario.tools,
        enable_transcription=True,
    )

    tool_call_count = 0
    real_handlers = get_seleccion_1_handlers(session)
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

    roleplay_turn_index = 0
    success = True
    failure_reason = ""

    # Evaluation values
    classifications: dict[int, str] = {}
    partial_turn = 0
    full_turn = 0
    has_opened_previously = False
    
    # Store initial state for context immutability validation
    initial_context_ref = None

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

                    # If we are in active roleplay, classify and validate
                    if session.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE:
                        if roleplay_turn_index > 0:
                            # 1. Capture initial reference context
                            if initial_context_ref is None:
                                initial_context_ref = dict(session.candidate_context)

                            # 2. Session Integrity Checks
                            if session.onboarding_phase != OnboardingPhase.ROLEPLAY_ACTIVE:
                                success = False
                                failure_reason = f"Left ROLEPLAY_ACTIVE state. Phase: {session.onboarding_phase}"
                                break
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

                            # 3. Classify response
                            category = classify_response(model_speech)
                            classifications[roleplay_turn_index] = category
                            transcript_lines.append(f"  [CLASSIFIED]: {category}")

                            # 4. Handle invalid categories
                            if category == "INVALID_ROLE_SWAP":
                                success = False
                                failure_reason = f"Role swap detected: Gemini acted as agent. Speech: '{model_speech}'"
                                break
                            if category == "INVALID_URGENCY":
                                success = False
                                failure_reason = f"Urgency claimed spontaneously! Speech: '{model_speech}'"
                                break
                            if category == "INVALID_CLOSURE":
                                success = False
                                failure_reason = f"Closure attempted early. Speech: '{model_speech}'"
                                break
                            if category == "UNKNOWN":
                                success = False
                                failure_reason = f"Response could not be clearly classified (UNKNOWN). Speech: '{model_speech}'"
                                break

                            # 5. Turn-by-turn expectations check
                            if roleplay_turn_index in [1, 2, 3, 4]:
                                # Must be RESISTANCE
                                if category != "RESISTANCE":
                                    success = False
                                    failure_reason = f"Opened up too early in Turn {roleplay_turn_index}. Category: {category}"
                                    break
                            
                            elif roleplay_turn_index == 5:
                                # Must be RESISTANCE or PARTIAL_OPENING
                                if category == "FULL_DISCLOSURE":
                                    success = False
                                    failure_reason = "Full disclosure occurred too early in Turn 5"
                                    break
                                if category == "PARTIAL_OPENING":
                                    partial_turn = 5
                                    has_opened_previously = True

                            elif roleplay_turn_index == 6:
                                # Must be RESISTANCE or PARTIAL_OPENING
                                if category == "FULL_DISCLOSURE":
                                    success = False
                                    failure_reason = "Full disclosure occurred too early in Turn 6"
                                    break
                                if category == "PARTIAL_OPENING" and partial_turn == 0:
                                    partial_turn = 6
                                    has_opened_previously = True

                            elif roleplay_turn_index == 7:
                                # Must be FULL_DISCLOSURE
                                if category != "FULL_DISCLOSURE":
                                    success = False
                                    failure_reason = f"Failed to reveal the query by Turn 7. Category: {category}"
                                    break
                                full_turn = 7

                            # 6. Check coherent progression
                            if category in ["PARTIAL_OPENING", "FULL_DISCLOSURE"]:
                                has_opened_previously = True
                            elif category == "RESISTANCE" and has_opened_previously:
                                success = False
                                failure_reason = f"Incoherent progression: Patient went back to absolute resistance in Turn {roleplay_turn_index} after opening"
                                break

                            # 7. Identity check on Intercambio 1 (Roleplay Turn 1)
                            if roleplay_turn_index == 1:
                                if not check_name_identity(model_speech):
                                    success = False
                                    failure_reason = f"Identity check failed. Response did not contain 'Miguel Pérez Gómez'. Speech: '{model_speech}'"
                                    break

                            # Exit loop if Turn 7 is completed
                            if roleplay_turn_index >= len(ROLEPLAY_INPUTS):
                                break

                    # ── Flow Turn Stepper ──
                    if session.onboarding_phase != OnboardingPhase.ROLEPLAY_ACTIVE:
                        # Dynamic stepper based on phase state
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
                        # Active Roleplay Sequential Stepper
                        if roleplay_turn_index < len(ROLEPLAY_INPUTS):
                            candidate_msg = ROLEPLAY_INPUTS[roleplay_turn_index]
                            roleplay_turn_index += 1
                            transcript_lines.append(f"USER: {candidate_msg}")
                            await session.send_text_turn(candidate_msg)
                        else:
                            break


    except Exception as exc:
        success = False
        failure_reason = f"Exception: {type(exc).__name__}: {exc}"
    finally:
        await session.close()

    if not success:
        transcript_lines.append(f"  [VAL_FAIL] Reason: {failure_reason}")

    pcm_24k = b"".join(audio_chunks_raw)
    mulaw_8k = b"".join(mulaw_chunks)
    
    # Assess naturalness of progression
    if success:
        naturalness = "Natural" if (partial_turn in [5, 6] and full_turn == 7) else "Brusca"
    else:
        naturalness = "N/A (Failed)"

    return success, transcript_lines, classifications, partial_turn, full_turn, naturalness, pcm_24k, mulaw_8k


async def main() -> int:
    print("============================================================", flush=True)
    print("  Scenario 'seleccion_1' Progressive Opening Validation (5 Runs)", flush=True)
    print("============================================================\n", flush=True)

    runs_success = []
    runs_data = []
    
    for i in range(1, 6):
        print(f"--- Executing Conversation {i} ---", flush=True)
        s, lines, classes, part, full, nat, pcm, mulaw = await run_single_execution(i)
        runs_success.append(s)
        runs_data.append((s, classes, part, full, nat))
        
        # Output conversation transcript to console
        print("\n".join(lines), flush=True)
        print(f"Conversation {i}: {'PASS' if s else 'FAIL'}\n", flush=True)
        
        # Save transcript to file
        path_txt = OUTPUT_DIR / f"seleccion_1_progressive_opening_run{i}.txt"
        path_txt.parent.mkdir(parents=True, exist_ok=True)
        path_txt.write_text("\n".join(lines), encoding="utf-8")
        
        # Save audio files only for Run 1
        if i == 1 and len(pcm) > 0:
            wav_24k = pcm_bytes_to_wav(pcm, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
            path_24k = OUTPUT_DIR / "seleccion_1_progressive_opening_run1_24khz.wav"
            path_24k.write_bytes(wav_24k)
            
            pcm_8k_bytes = mulaw_bytes_to_pcm(mulaw)
            wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
            path_8k = OUTPUT_DIR / "seleccion_1_progressive_opening_run1_8khz.wav"
            path_8k.write_bytes(wav_8k)

    print("============================================================", flush=True)
    print("  STABILITY REPORT - PROGRESSIVE OPENING", flush=True)
    print("============================================================", flush=True)
    
    for idx, (s, classes, part, full, nat) in enumerate(runs_data, 1):
        print(f"Ejecución {idx}:", flush=True)
        for t in range(1, 8):
            c_val = classes.get(t, "N/A")
            print(f"  Turno {t}: {c_val}", flush=True)
        print(f"  Apertura parcial: {'turno ' + str(part) if part > 0 else 'no'}", flush=True)
        print(f"  Revelación completa: {'turno ' + str(full) if full > 0 else 'no'}", flush=True)
        print(f"  Progresión: {nat}", flush=True)
        print(f"  Resultado: {'PASS' if s else 'FAIL'}\n", flush=True)

    all_passed = all(runs_success)
    print("============================================================", flush=True)
    print(f"  GLOBAL STATUS: {'PASS' if all_passed else 'FAIL'}", flush=True)
    print("============================================================", flush=True)

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
