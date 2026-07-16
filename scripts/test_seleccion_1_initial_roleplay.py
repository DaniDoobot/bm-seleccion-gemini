"""Roleplay simulation initial exchanges integration test script for scenario 'seleccion_1'.

Runs five independent executions of the onboarding + 4 active roleplay turns.
Verifies identity, role maintenance, resistance, no urgency claims, and no early revelation of the doubt.

Usage:
    .venv\\Scripts\\python.exe scripts\\test_seleccion_1_initial_roleplay.py
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

# ── Validation check helpers ──────────────────────────────────────────────────
def check_doubt_revealed(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    has_frequency = any(w in text_clean for w in ["limite", "frecuencia", "cuantas", "cuantos", "numero", "cantidad", "cuanto", "veces"])
    has_relations = any(w in text_clean for w in ["relaciones", "relacion", "coito", "sexo", "sexual", "sexuales"])
    has_weekly = any(w in text_clean for w in ["semana", "semanal", "semanales"])
    
    if has_frequency and has_relations and has_weekly:
        return True
        
    explicit_patterns = [
        "relaciones a la semana",
        "relaciones por semana",
        "frecuencia de relaciones",
        "frecuencia semanal"
    ]
    if any(pat in text_clean for pat in explicit_patterns):
        return True
    return False


def check_role_swap(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    agent_phrases = [
        "voy a revisar su caso", "voy a revisar tu caso",
        "voy a consultar su ficha", "voy a consultar tu ficha",
        "le voy a pasar", "te voy a pasar",
        "le pasare", "te pasare",
        "le transferire", "te transferire",
        "le pongo con el doctor", "te pongo con el doctor",
        "voy a transferir", "voy a pasarte",
        "desde atencion al paciente",
        "en que puedo ayudar", "en que le puedo ayudar", "en que te puedo ayudar",
        "facilite sus datos", "facilite tus datos",
        "la prueba ha terminado",
        "gracias por participar",
        "no puedo proporcionar feedback",
        "no puedo dar feedback"
    ]
    if any(phrase in text_clean for phrase in agent_phrases):
        return True
        
    if "usted es el paciente" in text_clean or "tu eres el paciente" in text_clean or "como usted es el paciente" in text_clean:
        return True
    return False


def check_transfer_action(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    agent_transfers = [
        "le voy a pasar", "te voy a pasar",
        "le pasare", "te pasare",
        "le transferire", "te transferire",
        "le pongo con el", "te pongo con el",
        "voy a transferir", "voy a pasarle", "voy a pasarte"
    ]
    if any(phrase in text_clean for phrase in agent_transfers):
        return True
    return False


def check_name_intercambio_1(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    return "miguel" in text_clean and "perez" in text_clean and "gomez" in text_clean


def check_resistance_intercambio_2(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    has_refusal = any(marker in text_clean for marker in [
        "hablar con el doctor", "hablar con el medico", "con el doctor", "con el medico",
        "tema personal", "asunto personal", "algo personal", "privado", "reservado",
        "hablar con el", "contar al doctor", "decir al doctor", "explicar al doctor"
    ])
    has_no_explain = ("no" in text_clean) and any(w in text_clean for w in ["contar", "explicar", "decir", "hablar", "dar", "revelar"])
    has_want_talk = any(w in text_clean for w in ["quiero", "prefiero", "necesito", "gustaria"]) and "hablar" in text_clean and any(w in text_clean for w in ["doctor", "medico", "el"])
    has_have_to = "tengo que" in text_clean and any(w in text_clean for w in ["hablar", "contar", "decir", "explicar"]) and any(w in text_clean for w in ["doctor", "medico", "el"])
    
    return has_refusal or has_no_explain or has_want_talk or has_have_to


def check_cession_intercambio_3(text: str) -> bool:
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    cession_phrases = [
        "vale te lo cuento",
        "vale te cuento",
        "te lo voy a contar",
        "si puedes ayudarme tu",
        "puedes ayudarme tu",
        "ya no necesito hablar con el doctor",
        "ya no hace falta hablar con el doctor"
    ]
    return any(phrase in text_clean for phrase in cession_phrases)


def check_urgency(text: str) -> bool:
    """Verify if the patient spontaneously claims urgency (which is forbidden)."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    urgency_triggers = [
        "es urgente", "es una urgencia", "tengo una urgencia",
        "urgente que me atienda", "atencion medica urgente",
        "es algo grave", "pasando algo grave", "es grave"
    ]
    for trigger in urgency_triggers:
        if trigger in text_clean:
            idx = text_clean.find(trigger)
            prefix = text_clean[max(0, idx-15):idx]
            if any(neg in prefix for neg in ["no ", "no es ", "tampoco ", "nunca ", "sin "]):
                continue
            return True
    return False


# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seleccion_1_initial_roleplay_test")

# ── Output directory ──────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "audio-tests"

# ── Timeout configuration (seconds) ──────────────────────────────────────────
GLOBAL_TIMEOUT_SECONDS = 150

ROLEPLAY_INPUTS = [
    "Buenos días. Antes de nada, ¿me puede indicar su nombre completo?",
    "Gracias, Miguel. Para poder ayudarle necesito que me explique brevemente qué ocurre.",
    "Entiendo que sea algo personal, pero necesito conocer el motivo para saber cómo podemos ayudarle.",
    "Mi intención es ayudarle y evitarle más esperas, pero necesito que me dé algo más de información."
]


async def run_single_execution(exec_num: int) -> tuple[bool, list[str], bytes, bytes]:
    """Run a single onboarding and 4-turn roleplay execution.

    Returns:
        tuple (success_status, transcript_lines, pcm_24k_bytes, mulaw_8k_bytes)
    """
    scenario = get_scenario("seleccion_1")
    if not scenario:
        return False, ["Scenario seleccion_1 not found"], b"", b""

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

                    # Validate active roleplay criteria if we are in active roleplay
                    if session.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE:
                        # 1. State integrity check
                        if session.onboarding_phase != OnboardingPhase.ROLEPLAY_ACTIVE:
                            success = False
                            failure_reason = f"Left ROLEPLAY_ACTIVE phase. Current phase: {session.onboarding_phase}"
                            break

                        # 2. Tool count check (must stay at exactly 1)
                        if tool_call_count != 1:
                            success = False
                            failure_reason = f"Tool executed {tool_call_count} times instead of exactly 1"
                            break

                        # 3. Context immutability check
                        if not session.candidate_context.get("saved") or session.candidate_context.get("caller_user_name") != "Daniel":
                            success = False
                            failure_reason = f"Candidate context was modified or cleared: {session.candidate_context}"
                            break

                        # 4. Role swap check
                        if check_role_swap(model_speech):
                            success = False
                            failure_reason = f"Role swap detected: Gemini acted as agent or evaluator. Speech: '{model_speech}'"
                            break

                        # 5. Transfer action check
                        if check_transfer_action(model_speech):
                            success = False
                            failure_reason = f"Transfer action check failed: Gemini offered to perform transfer. Speech: '{model_speech}'"
                            break

                        # 6. Early doubt revelation check
                        if check_doubt_revealed(model_speech):
                            success = False
                            failure_reason = f"Doubt revealed prematurely! Speech: '{model_speech}'"
                            break

                        # 7. Urgency check
                        if check_urgency(model_speech):
                            success = False
                            failure_reason = f"Urgency claimed spontaneously! Speech: '{model_speech}'"
                            break

                        # 8. Cierre/feedback check
                        if any(k in model_speech for k in ["La prueba ha terminado", "Gracias por participar", "No puedo proporcionar feedback"]):
                            success = False
                            failure_reason = f"Gemini attempted to close the test or give feedback. Speech: '{model_speech}'"
                            break

                        # ── Specific Turn Validations ──
                        if roleplay_turn_index == 1:
                            # Intercambio 1: Expect full name
                            if not check_name_intercambio_1(model_speech):
                                success = False
                                failure_reason = f"Intercambio 1 name check failed. Response did not contain 'Miguel Pérez Gómez'. Speech: '{model_speech}'"
                                break
                            transcript_lines.append(f"  [VAL] Intercambio 1: PASS")

                        elif roleplay_turn_index == 2:
                            # Intercambio 2: Expect resistance
                            if not check_resistance_intercambio_2(model_speech):
                                success = False
                                failure_reason = f"Intercambio 2 resistance check failed. Response did not refuse to explain motive. Speech: '{model_speech}'"
                                break
                            transcript_lines.append(f"  [VAL] Intercambio 2: PASS")

                        elif roleplay_turn_index == 3:
                            # Intercambio 3: Expect cession check (no total cession)
                            if check_cession_intercambio_3(model_speech):
                                success = False
                                failure_reason = f"Intercambio 3 cession check failed. Patient ceded completely. Speech: '{model_speech}'"
                                break
                            transcript_lines.append(f"  [VAL] Intercambio 3: PASS")

                        elif roleplay_turn_index == 4:
                            # Intercambio 4: Expect no doubt revelation (already checked by check_doubt_revealed)
                            transcript_lines.append(f"  [VAL] Intercambio 4: PASS")

                        # Exit if we completed Turn 4
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
                            # Waiting
                            pass
                    else:
                        # Active Roleplay
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
    return success, transcript_lines, pcm_24k, mulaw_8k


async def main() -> int:
    print("============================================================", flush=True)
    print("  Scenario 'seleccion_1' Initial Roleplay Stability Test (5 Runs)", flush=True)
    print("============================================================\n", flush=True)

    runs_success = []
    
    # Run 1
    print("--- Executing Conversation 1 ---", flush=True)
    s1, lines1, pcm1, mulaw1 = await run_single_execution(1)
    runs_success.append(s1)
    print("\n".join(lines1), flush=True)
    print(f"Conversation 1: {'PASS' if s1 else 'FAIL'}\n", flush=True)
    
    # Save Output Files for Run 1
    if len(pcm1) > 0:
        wav_24k = pcm_bytes_to_wav(pcm1, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
        path_24k = OUTPUT_DIR / "seleccion_1_initial_roleplay_24khz.wav"
        path_24k.parent.mkdir(parents=True, exist_ok=True)
        path_24k.write_bytes(wav_24k)
        
        pcm_8k_bytes = mulaw_bytes_to_pcm(mulaw1)
        wav_8k = pcm_bytes_to_wav(pcm_8k_bytes, sample_rate=TWILIO_SAMPLE_RATE)
        path_8k = OUTPUT_DIR / "seleccion_1_initial_roleplay_8khz.wav"
        path_8k.write_bytes(wav_8k)
        
        path_txt = OUTPUT_DIR / "seleccion_1_initial_roleplay.txt"
        path_txt.write_text("\n".join(lines1), encoding="utf-8")

    # Run 2
    print("--- Executing Conversation 2 ---", flush=True)
    s2, lines2, pcm2, mulaw2 = await run_single_execution(2)
    runs_success.append(s2)
    print("\n".join(lines2), flush=True)
    print(f"Conversation 2: {'PASS' if s2 else 'FAIL'}\n", flush=True)
    
    path_txt_2 = OUTPUT_DIR / "seleccion_1_initial_roleplay_run2.txt"
    path_txt_2.write_text("\n".join(lines2), encoding="utf-8")

    # Run 3
    print("--- Executing Conversation 3 ---", flush=True)
    s3, lines3, pcm3, mulaw3 = await run_single_execution(3)
    runs_success.append(s3)
    print("\n".join(lines3), flush=True)
    print(f"Conversation 3: {'PASS' if s3 else 'FAIL'}\n", flush=True)
    
    path_txt_3 = OUTPUT_DIR / "seleccion_1_initial_roleplay_run3.txt"
    path_txt_3.write_text("\n".join(lines3), encoding="utf-8")

    # Run 4
    print("--- Executing Conversation 4 ---", flush=True)
    s4, lines4, pcm4, mulaw4 = await run_single_execution(4)
    runs_success.append(s4)
    print("\n".join(lines4), flush=True)
    print(f"Conversation 4: {'PASS' if s4 else 'FAIL'}\n", flush=True)
    
    path_txt_4 = OUTPUT_DIR / "seleccion_1_initial_roleplay_run4.txt"
    path_txt_4.write_text("\n".join(lines4), encoding="utf-8")

    # Run 5
    print("--- Executing Conversation 5 ---", flush=True)
    s5, lines5, pcm5, mulaw5 = await run_single_execution(5)
    runs_success.append(s5)
    print("\n".join(lines5), flush=True)
    print(f"Conversation 5: {'PASS' if s5 else 'FAIL'}\n", flush=True)
    
    path_txt_5 = OUTPUT_DIR / "seleccion_1_initial_roleplay_run5.txt"
    path_txt_5.write_text("\n".join(lines5), encoding="utf-8")

    print("============================================================", flush=True)
    print("  STABILITY REPORT", flush=True)
    print("============================================================", flush=True)
    print(f"  Ejecución 1: {'PASS' if s1 else 'FAIL'}", flush=True)
    print(f"  Ejecución 2: {'PASS' if s2 else 'FAIL'}", flush=True)
    print(f"  Ejecución 3: {'PASS' if s3 else 'FAIL'}", flush=True)
    print(f"  Ejecución 4: {'PASS' if s4 else 'FAIL'}", flush=True)
    print(f"  Ejecución 5: {'PASS' if s5 else 'FAIL'}", flush=True)
    print("============================================================", flush=True)

    all_passed = all(runs_success)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
