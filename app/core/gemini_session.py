"""Core Gemini Live session manager.

Responsibilities:
  - Build the Gemini BidiGenerateContent setup message with optional tools
    and transcription modalities.
  - Open and maintain the WebSocket connection to Gemini.
  - Expose methods to send audio chunks, text turns, and tool responses.
  - Support function calling by registering handlers, executing them,
    and responding to the model automatically.
  - Yield model outputs (audio chunks, text transcripts, interruption signals,
    setup-complete events, turn-complete signals).
  - Manage a session-specific candidate_context state.
  - Close and clean up the connection.
"""
import asyncio
from enum import Enum
import json
import logging
import threading
from typing import Any, AsyncIterator, Dict, List, Optional

import websockets
import websockets.exceptions

from app.config import Settings

logger = logging.getLogger(__name__)

# ── Gemini event type constants ───────────────────────────────────────────────
GEMINI_SETUP_COMPLETE = "setupComplete"
GEMINI_SERVER_CONTENT = "serverContent"
GEMINI_TOOL_CALL = "toolCall"


class OnboardingPhase(str, Enum):
    WAITING_READY = "waiting_ready"
    WAITING_CANDIDATE_DATA = "waiting_candidate_data"
    WAITING_DATA_CONFIRMATION = "waiting_data_confirmation"
    READY_TO_ASK_RGPD = "ready_to_ask_rgpd"
    WAITING_RGPD_ACCEPTANCE = "waiting_rgpd_acceptance"
    READY_TO_SAVE = "ready_to_save"
    CONTEXT_SAVED = "context_saved"
    EXPLANATION = "explanation"
    READY_TO_START_ROLEPLAY = "ready_to_start_roleplay"
    ROLEPLAY_ACTIVE = "roleplay_active"
    ROLEPLAY_FINISHED = "roleplay_finished"


class GeminiVoiceSession:
    """Thin wrapper around the Gemini Live BidiGenerateContent WebSocket."""

    def __init__(
        self,
        settings: Settings,
        system_instruction: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        enable_transcription: bool = False,
        roleplay_transition_phrase: str = "Perfecto, comenzamos la simulación. A partir de ahora soy el paciente.",
        roleplay_initial_phrase: str = "Mira, quiero hablar con el doctor ahora mismo.",
        completion_phrase: str = "La prueba ha terminado. Gracias por participar.",
    ) -> None:
        """Initialize the Gemini Live session wrapper."""
        self._settings = settings
        self._system_instruction = system_instruction
        self._tools = tools or []
        self._enable_transcription = settings.gemini_transcription_enabled
        self._roleplay_transition_phrase = roleplay_transition_phrase
        self._roleplay_initial_phrase = roleplay_initial_phrase
        self._completion_phrase = completion_phrase
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ready = False

        # Registered function/tool handlers: name -> callable
        self._tool_handlers: Dict[str, Any] = {}

        # Candidate context state (provisional in-memory store)
        self.candidate_context = {
            "caller_user_name": None,
            "caller_user_lastname": None,
            "rgpd_ok": None,
            "scenario": None,
            "saved": False,
        }

        self.onboarding_phase = OnboardingPhase.WAITING_READY
        self.provisional_name = None
        self.provisional_lastname = None

        # Call recording / integration fields
        self.call_sid: Optional[str] = None
        self.stream_sid: Optional[str] = None
        self.twilio_account_sid: Optional[str] = None
        self.recording_sid: Optional[str] = None
        self.recording_started: bool = False
        self.recording_start_attempted = False
        self.recording_status = "none"
        self.recording_error = None

        self.transcript = []
        self._commit_lock = threading.Lock()

        self._user_transcript_accumulator = ""
        self._model_transcript_accumulator = ""


    # ── Onboarding State Machine Transitions ──────────────────────────────────

    def process_user_transcript(self, text: str) -> None:
        """Process a candidate/user turn to update the onboarding phase."""
        clean_text = text.strip()
        if not clean_text:
            return

        last_user = getattr(self, "_last_user_transcript", None)
        if clean_text == last_user:
            return
        self._last_user_transcript = clean_text

        logger.info(
            "Candidate turn completed. phase=%s chars=%d",
            self.onboarding_phase.value if hasattr(self.onboarding_phase, "value") else str(self.onboarding_phase),
            len(clean_text)
        )
        if getattr(self._settings, "log_transcripts", False):
            logger.debug("[Onboarding] Candidate turn: '%s'", clean_text)

        old_phase = self.onboarding_phase
        self._update_onboarding_state(clean_text, is_user=True)
        if old_phase != self.onboarding_phase:
            logger.info(
                "Phase transitioned from %s to %s",
                old_phase.value if hasattr(old_phase, "value") else str(old_phase),
                self.onboarding_phase.value if hasattr(self.onboarding_phase, "value") else str(self.onboarding_phase)
            )

    def process_model_transcript(self, text: str) -> None:
        """Process a Gemini/model turn to update the onboarding phase."""
        clean_text = text.strip()
        if not clean_text:
            return

        last_model = getattr(self, "_last_model_transcript", None)
        if clean_text == last_model:
            return
        self._last_model_transcript = clean_text

        logger.info(
            "Patient turn completed. phase=%s chars=%d",
            self.onboarding_phase.value if hasattr(self.onboarding_phase, "value") else str(self.onboarding_phase),
            len(clean_text)
        )
        if getattr(self._settings, "log_transcripts", False):
            logger.debug("[Onboarding] Gemini turn: '%s'", clean_text)

        old_phase = self.onboarding_phase
        self._update_onboarding_state(clean_text, is_user=False)
        if old_phase != self.onboarding_phase:
            logger.info(
                "Phase transitioned from %s to %s",
                old_phase.value if hasattr(old_phase, "value") else str(old_phase),
                self.onboarding_phase.value if hasattr(self.onboarding_phase, "value") else str(self.onboarding_phase)
            )

    def _update_onboarding_state(self, text: str, is_user: bool) -> None:
        text_lower = text.lower()
        for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
            text_lower = text_lower.replace(a, b)

        if is_user:
            if self.onboarding_phase == OnboardingPhase.WAITING_READY:
                # Accept positive responses only. Must not contain negative markers.
                if ("si" in text_lower or "preparado" in text_lower or "comenzar" in text_lower or "listo" in text_lower) and "no " not in text_lower and text_lower != "no":
                    self.onboarding_phase = OnboardingPhase.WAITING_CANDIDATE_DATA

            elif self.onboarding_phase == OnboardingPhase.WAITING_CANDIDATE_DATA:
                # Expect at least two components representing name and lastname.
                # Must not look like a general greeting or short yes/no/consent response.
                words_lower = []
                for w in text.split():
                    if w:
                        w_clean = w.strip(".,¡!¿?()\"';:").lower()
                        for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
                            w_clean = w_clean.replace(a, b)
                        words_lower.append(w_clean)
                if any(w in words_lower for w in ["si", "no", "acepto", "aceptar", "correcto"]):
                    return
                words = [w.strip(".,¡!¿?()\"';:") for w in text.split() if w]
                phrase_indicators = {"hola", "buenos", "dias", "tardes", "soy", "llamo", "mi", "nombre", "es"}
                filtered = [w for w in words if w.lower() not in phrase_indicators]
                if len(filtered) >= 2:
                    self.provisional_name = filtered[0].capitalize()
                    self.provisional_lastname = " ".join(filtered[1:]).title()
                    self.onboarding_phase = OnboardingPhase.WAITING_DATA_CONFIRMATION

            elif self.onboarding_phase == OnboardingPhase.WAITING_DATA_CONFIRMATION:
                # Check for corrections or negatives
                is_correction = any(k in text_lower for k in ["no", "incorrecto", "cambiar", "apellido no es", "nombre no es", "no es correcto"])
                if is_correction:
                    # Update provisional name/lastname if corrected values were supplied in the same turn
                    words = [w.strip(".,¡!¿?()\"';:") for w in text.split() if w]
                    filtered = [w for w in words if w.lower() not in {"no", "es", "pero", "mi", "cambio", "apellido", "nombre", "el"}]
                    if len(filtered) >= 2:
                        self.provisional_name = filtered[0].capitalize()
                        self.provisional_lastname = " ".join(filtered[1:]).title()
                    logger.info("[Onboarding] Candidate corrected name. Staying in WAITING_DATA_CONFIRMATION.")
                else:
                    # Positive confirmation
                    if "correcto" in text_lower or text_lower.strip() in ["si", "correcto", "de acuerdo", "vale", "asi es"]:
                        self.onboarding_phase = OnboardingPhase.READY_TO_ASK_RGPD

            elif self.onboarding_phase == OnboardingPhase.WAITING_RGPD_ACCEPTANCE:
                # Explicit consent only: "acepto", "aceptar", "consentimiento", "consiento".
                # Simple "si", "correcto", "vale", "de acuerdo" are blocked.
                explicit_consent = any(k in text_lower for k in ["acepto", "aceptar", "consentimiento", "consiento"])
                if explicit_consent:
                    self.onboarding_phase = OnboardingPhase.READY_TO_SAVE
                    # Trigger deterministic context save immediately
                    self.commit_candidate_context()

            elif self.onboarding_phase == OnboardingPhase.EXPLANATION:
                # Strip known positive start phrases that might contain words like "duda" or "repitas"
                check_text = text_lower
                for p in ["no tengo dudas", "no tengo duda", "no necesito que repitas", "sin dudas", "sin duda", "ninguna duda"]:
                    check_text = check_text.replace(p, "")
                has_doubt = any(k in check_text for k in ["duda", "pregunta", "repetir", "repitas", "no podemos", "todavia no", "no entendi", "tengo una"])
                if not has_doubt:
                    # Positive start intent directly transitions to READY_TO_START_ROLEPLAY for compatibility
                    start_intent = any(k in text_lower for k in [
                        "si", "sí", "correcto", "comenzar", "empezar", "listo", "preparado", "entendido", "ninguna duda", "no tengo dudas", "adelante", "comenzamos"
                    ])
                    if start_intent:
                        self.onboarding_phase = OnboardingPhase.READY_TO_START_ROLEPLAY

            elif self.onboarding_phase == OnboardingPhase.READY_TO_START_ROLEPLAY:
                # Candidate confirms they are prepared / understood instructions
                check_text = text_lower
                for p in ["no tengo dudas", "no tengo duda", "no necesito que repitas", "sin dudas", "sin duda", "ninguna duda"]:
                    check_text = check_text.replace(p, "")
                has_doubt_or_negation = any(k in check_text for k in [
                    "duda", "pregunta", "repetir", "repitas", "no podemos", "todavia no", "no entendi"
                ])
                if has_doubt_or_negation:
                    # Return to EXPLANATION phase
                    self.onboarding_phase = OnboardingPhase.EXPLANATION
                else:
                    start_intent = any(k in text_lower for k in [
                        "si", "sí", "correcto", "comenzar", "empezar", "listo", "preparado", "entendido", "ninguna duda", "no tengo dudas", "adelante", "comenzamos"
                    ])
                    if start_intent:
                        self.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE

        else:
            # Model turns
            if self.onboarding_phase == OnboardingPhase.READY_TO_ASK_RGPD:
                # Transition to WAITING_RGPD_ACCEPTANCE when Gemini asks the RGPD question.
                # Must match at least 2 essential elements.
                elements = ["cumplimiento rgpd", "realizacion de esta prueba", "grabacion", "aceptas ambas cosas", "grabar", "aceptacion"]
                matches = sum(1 for e in elements if e in text_lower)
                if matches >= 2:
                    self.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE

            elif self.onboarding_phase == OnboardingPhase.CONTEXT_SAVED:
                # Any model turn transitions to EXPLANATION
                self.onboarding_phase = OnboardingPhase.EXPLANATION

            elif self.onboarding_phase == OnboardingPhase.EXPLANATION:
                # Transition to READY_TO_START_ROLEPLAY when Gemini asks the confirmation question.
                elements = ["claro", "duda", "pregunta", "entendido", "comenzar", "empezar", "listo", "preparado"]
                if any(e in text_lower for e in elements):
                    self.onboarding_phase = OnboardingPhase.READY_TO_START_ROLEPLAY

    def _normalize_text(self, text: str) -> str:
        """Helper to normalize text for string matching, stripping punctuation and accents."""
        import re
        text_lower = text.lower()
        for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
            text_lower = text_lower.replace(a, b)
        text_clean = re.sub(r'[.,¡!¿?()"\';:-]', ' ', text_lower)
        text_clean = ' '.join(text_clean.split())
        return text_clean

    # ── Tool handlers registration ────────────────────────────────────────────

    def register_tool_handler(self, name: str, handler: Any) -> None:
        """Register a handler for a tool/function call.

        Args:
            name: The function name (e.g. 'save_candidate_context').
            handler: Callable taking arguments dict and returning dict output.
        """
        self._tool_handlers[name] = handler
        logger.debug("Registered tool handler for: %s", name)

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the WebSocket to Gemini and send the setup message.

        Raises:
            ValueError: If GEMINI_API_KEY is not configured.
            websockets.exceptions.WebSocketException: On connection failure.
        """
        if not self._settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is not configured. "
                "Set it in your .env file or environment."
            )

        url = self._settings.gemini_ws_url
        logger.info(
            "Connecting to Gemini Live. model=%s voice=%s tools=%d transcription=%s",
            self._settings.gemini_model,
            self._settings.gemini_voice_name,
            len(self._tools),
            self._enable_transcription,
        )
        self._ws = await websockets.connect(url)
        await self._send_setup()
        logger.info("Gemini Live WebSocket connected and setup message sent.")

    async def close(self) -> None:
        """Close the Gemini WebSocket connection gracefully."""
        if self._ws is not None:
            try:
                await self._ws.close()
                logger.info("Gemini Live WebSocket closed.")
            except Exception:
                pass  # Already closed or connection was reset
        self._ws = None
        self._ready = False

    # ── Async context manager ─────────────────────────────────────────────────

    async def __aenter__(self) -> "GeminiVoiceSession":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Public API ────────────────────────────────────────────────────────────

    async def send_audio(self, base64_pcm_16k: str) -> None:
        """Send a base64-encoded 16 kHz PCM audio chunk to Gemini.

        Args:
            base64_pcm_16k: Base64-encoded linear PCM audio at 16 kHz.
        """
        if self._ws is None:
            logger.warning("send_audio called but WebSocket is not open.")
            return

        msg = {
            "realtimeInput": {
                "audio": {
                    "mimeType": "audio/pcm;rate=16000",
                    "data": base64_pcm_16k,
                }
            }
        }
        await self._ws.send(json.dumps(msg))

    async def send_text_turn(self, text: str) -> None:
        """Send a complete text turn to Gemini and mark it as turn-complete.

        Args:
            text: The text content to send as the user's turn.
        """
        if self._ws is None:
            logger.warning("send_text_turn called but WebSocket is not open.")
            return

        self._user_transcript_accumulator = ""
        self._model_transcript_accumulator = ""
        
        # Skip processing if it's the greeting trigger prompt
        text_lower = text.lower()
        if "di unicamente" not in text_lower and "sin preambulos" not in text_lower and "di únicamente" not in text_lower and "sin preámbulos" not in text_lower:
            self.process_user_transcript(text)

        msg = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": text}],
                    }
                ],
                "turnComplete": True,
            }
        }
        await self._ws.send(json.dumps(msg))
        logger.debug("Text turn sent to Gemini: %.80s...", text)

    async def send_function_response(self, name: str, call_id: str, output: dict) -> None:
        """Send a function response (toolResponse) back to Gemini Live.

        Args:
            name: Name of the function.
            call_id: Unique call ID sent by the model.
            output: Dictionary representing the output payload of the function.
        """
        if self._ws is None:
            logger.warning("send_function_response called but WebSocket is not open.")
            return

        msg = {
            "toolResponse": {
                "functionResponses": [
                    {
                        "name": name,
                        "id": call_id,
                        "response": {
                            "output": output,
                            "result": output,
                            **output
                        }
                    }
                ]
            }
        }
        await self._ws.send(json.dumps(msg))
        logger.debug("Sent toolResponse for %s (id: %s)", name, call_id)

    def add_transcript_turn(self, speaker: str, text: str) -> None:
        """Add a consolidated turn to the in-memory transcript."""
        import datetime
        
        clean_text = text.strip()
        if not clean_text:
            return
        
        # Calculate phase: "roleplay" if phase is active/finished, otherwise "onboarding"
        is_roleplay = self.onboarding_phase in [OnboardingPhase.ROLEPLAY_ACTIVE, OnboardingPhase.ROLEPLAY_FINISHED]
        phase = "roleplay" if is_roleplay else "onboarding"
        
        self.transcript.append({
            "sequence": len(self.transcript) + 1,
            "speaker": speaker,
            "phase": phase,
            "text": clean_text,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        })

    def commit_candidate_context(self) -> dict:
        """Idempotent helper to persist candidate details, set CONTEXT_SAVED phase, and launch Twilio recording."""
        with self._commit_lock:
            if self.candidate_context.get("saved"):
                logger.info("[Onboarding] Candidate context already saved. Skipping duplicate save.")
                return {
                    "status": "already_saved",
                    "success": True,
                    "message": "Candidate context was already saved for this session.",
                    "caller_user_name": self.candidate_context.get("caller_user_name"),
                    "caller_user_lastname": self.candidate_context.get("caller_user_lastname"),
                }

            name = getattr(self, "provisional_name", "") or ""
            lastname = getattr(self, "provisional_lastname", "") or ""
            name_str = str(name).strip()
            lastname_str = str(lastname).strip()

            if not name_str or not lastname_str:
                logger.warning("[Onboarding] commit_candidate_context failed because candidate name/lastname are missing.")
                return {
                    "status": "missing_data",
                    "success": False,
                    "message": "Faltan el nombre o los apellidos del candidato."
                }

            self.candidate_context.update({
                "caller_user_name": name_str,
                "caller_user_lastname": lastname_str,
                "rgpd_ok": True,
                "scenario": getattr(self, "scenario_id", "seleccion_1"),
                "saved": True
            })
            self.onboarding_phase = OnboardingPhase.CONTEXT_SAVED
            logger.info("RGPD accepted and candidate context saved")

            call_sid = getattr(self, "call_sid", None)
            if call_sid and not getattr(self, "recording_start_attempted", False):
                self.recording_start_attempted = True
                from app.services.twilio_recording import start_twilio_recording
                try:
                    logger.info("Triggering Twilio recording. CallSid=%s", call_sid)
                    scenario_id = getattr(self, "scenario_id", "seleccion_1")
                    rec_sid = start_twilio_recording(call_sid, scenario_id)
                    if rec_sid:
                        self.recording_sid = rec_sid
                        self.recording_started = True
                        self.recording_status = "in_progress"
                        logger.info("Recording started. call_sid=%s recording_sid=%s", call_sid, rec_sid)
                    else:
                        self.recording_error = "failed_to_start"
                        logger.warning("Recording start failed for CallSid=%s", call_sid)
                except Exception as e:
                    logger.error("Error starting recording: %s", e)

            # Inform Gemini of the commit and force explanation phase transition
            instruction = (
                "[SISTEMA: El contexto del candidato y su aceptación RGPD han sido guardados correctamente en el sistema. "
                "La grabación de la llamada ha comenzado con éxito. Procede de inmediato a explicar detalladamente al candidato "
                "las instrucciones de la prueba y la situación del roleplay, hablando en tu papel de paciente de forma natural.]"
            )
            
            async def _send_instruction():
                try:
                    await self.send_text_turn(instruction)
                    logger.info("[Onboarding] Forced explanation instruction to Gemini Live.")
                except Exception as e:
                    logger.error("[Onboarding] Error sending explanation instruction to Gemini: %s", e)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send_instruction())
            except RuntimeError:
                pass  # Ignore if no running loop (e.g. unit tests)

            return {
                "status": "saved",
                "success": True,
                "message": "Candidate context saved successfully."
            }

    async def reconnect(self) -> None:
        """Reconnect to Gemini Live and restore the state of the conversation."""
        logger.info("[Session] Attempting transient reconnect to Gemini Live...")
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._ready = False

        await self.connect()

        # Build and send internal state summary
        name = self.candidate_context.get("caller_user_name", "")
        lastname = self.candidate_context.get("caller_user_lastname", "")
        phase = self.onboarding_phase

        state_info = f"[SISTEMA: RECONEXIÓN DETECTADA. La sesión con el candidato se ha restablecido. "
        state_info += f"Candidato: {name} {lastname}. "
        state_info += f"Fase de onboarding actual: {phase.value}. "

        if phase in [OnboardingPhase.CONTEXT_SAVED, OnboardingPhase.EXPLANATION]:
            state_info += "El contexto y la aceptación RGPD ya están guardados en el sistema. La grabación está en curso. "
            state_info += "No vuelvas a pedir el nombre, apellidos ni RGPD. Procede o continúa explicando las instrucciones de la prueba y la situación del roleplay al candidato de forma natural en tu personaje."
        elif phase == OnboardingPhase.ROLEPLAY_ACTIVE:
            state_info += "El onboarding se ha completado y el roleplay está ACTIVO. El candidato ya está interactuando en el papel. "
            state_info += "Continúa la simulación en tu personaje de paciente de forma natural, retomando la conversación desde el punto en el que se cortó."
        elif phase == OnboardingPhase.ROLEPLAY_FINISHED:
            state_info += "La simulación ha finalizado. Despídete formalmente en personaje."
        else:
            state_info += "Aún no se ha completado el onboarding. Continúa el flujo de onboarding en la pregunta correspondiente a la fase indicada (nombre, confirmación de datos o aceptación RGPD) de manera natural."

        state_info += "]"

        try:
            await self.send_text_turn(state_info)
            logger.info("[Session] Sent reconnection state info to Gemini Live.")
        except Exception as e:
            logger.error("[Session] Failed to send reconnection state info: %s", e)

    async def receive(self) -> AsyncIterator[dict]:

        """Yield normalized event dicts from the Gemini WebSocket.

        Yielded event shapes:
          {"type": "setup_complete"}
          {"type": "audio", "data": "<base64-pcm-24k>"}
          {"type": "text", "data": "<transcribed-speech>"}
          {"type": "interrupted"}
          {"type": "turn_complete"}              — model finished its turn
          {"type": "tool_call", "name": "...", "args": {...}, "id": "..."}
          {"type": "unknown", "raw": <dict>}    — unrecognised messages

        Raises:
            RuntimeError: If called before connect().
        """
        if not self._ws:
            raise RuntimeError("GeminiVoiceSession.connect() must be called first.")

        async for raw_msg in self._ws:
            try:
                data = json.loads(raw_msg)
            except json.JSONDecodeError as exc:
                logger.error("Failed to parse Gemini message: %s", exc)
                continue

            # ── Setup complete ────────────────────────────────────────────────
            if GEMINI_SETUP_COMPLETE in data:
                self._ready = True
                logger.info("Gemini Live setup complete.")
                yield {"type": "setup_complete"}

            # ── Server content (audio + transcriptions + interruptions) ───────
            elif GEMINI_SERVER_CONTENT in data:
                content = data[GEMINI_SERVER_CONTENT]

                # Barge-in: model was interrupted by user speech
                if content.get("interrupted"):
                    logger.debug("Gemini signalled interruption.")
                    yield {"type": "interrupted"}

                # Parts from the model's current turn
                model_turn = content.get("modelTurn")
                if model_turn:
                    # Model starts responding, clear user transcript accumulator
                    user_text = self._user_transcript_accumulator.strip()
                    if user_text:
                        self.add_transcript_turn("candidate", user_text)
                    self._user_transcript_accumulator = ""
                    for part in model_turn.get("parts", []):
                        # Spoken Audio
                        inline_data = part.get("inlineData")
                        if inline_data and inline_data.get("data"):
                            yield {
                                "type": "audio",
                                "data": inline_data["data"],
                            }
                        # Text Transcript (fallback if present in part)
                        if "text" in part:
                            yield {
                                "type": "text",
                                "data": part["text"],
                            }

                # inputTranscription (real-time speech-to-text transcript of user audio input)
                input_trans = content.get("inputTranscription")
                if input_trans and "text" in input_trans:
                    self._user_transcript_accumulator += " " + input_trans["text"]
                    self.process_user_transcript(self._user_transcript_accumulator.strip())
                    yield {
                        "type": "text_input",
                        "data": input_trans["text"],
                    }

                # outputTranscription (real-time speech-to-text transcript of model response)
                output_trans = content.get("outputTranscription")
                if output_trans and "text" in output_trans:
                    self._model_transcript_accumulator += " " + output_trans["text"]
                    self.process_model_transcript(self._model_transcript_accumulator.strip())
                    yield {
                        "type": "text",
                        "data": output_trans["text"],
                    }

                # Model signals that its turn is complete
                if content.get("turnComplete"):
                    logger.debug("Gemini turn complete.")
                    # Consolidated turn save for model/patient:
                    model_text = self._model_transcript_accumulator.strip()
                    if model_text:
                        self.add_transcript_turn("patient", model_text)

                    # Check transition to ROLEPLAY_ACTIVE at turnComplete
                    if self.onboarding_phase == OnboardingPhase.READY_TO_START_ROLEPLAY:
                        norm_accumulated = self._normalize_text(self._model_transcript_accumulator)
                        norm_p1 = self._normalize_text(self._roleplay_transition_phrase)
                        norm_p2 = self._normalize_text(self._roleplay_initial_phrase)
                        p1_idx = norm_accumulated.find(norm_p1)
                        p2_idx = norm_accumulated.find(norm_p2)
                        
                        if p1_idx != -1 and p2_idx != -1 and p1_idx < p2_idx:
                            self.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE
                            logger.info("[Onboarding] Roleplay started successfully. Phase: ROLEPLAY_ACTIVE")
                    elif self.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE:
                        norm_accumulated = self._normalize_text(self._model_transcript_accumulator)
                        has_terminado = "la prueba ha terminado" in norm_accumulated or "la prueba ha finalizado" in norm_accumulated
                        has_gracias = "gracias por participar" in norm_accumulated or "gracias por su participacion" in norm_accumulated
                        if (has_terminado and has_gracias) or ("la prueba ha terminado gracias por participar" in norm_accumulated):
                            self.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
                            logger.info("[Onboarding] Roleplay finished successfully. Phase: ROLEPLAY_FINISHED")
                    
                    self._model_transcript_accumulator = ""
                    self._user_transcript_accumulator = ""
                    yield {"type": "turn_complete"}

            # ── Tool calls ────────────────────────────────────────────────────

            elif GEMINI_TOOL_CALL in data:
                tool_call = data[GEMINI_TOOL_CALL]
                function_calls = tool_call.get("functionCalls", [])

                for fc in function_calls:
                    name = fc.get("name")
                    args = fc.get("args", {})
                    call_id = fc.get("id")

                    logger.debug("Received toolCall request: name=%s, id=%s", name, call_id)

                    # If a handler is registered, execute it and reply immediately
                    if name in self._tool_handlers:
                        try:
                            handler = self._tool_handlers[name]
                            if asyncio.iscoroutinefunction(handler):
                                output = await handler(args)
                            else:
                                output = handler(args)

                            await self.send_function_response(name, call_id, output)
                        except Exception as exc:
                            logger.error("Error executing handler for %s: %s", name, exc)
                            await self.send_function_response(
                                name, call_id, {"error": str(exc)}
                            )
                    else:
                        # Otherwise yield to the caller
                        yield {
                            "type": "tool_call",
                            "name": name,
                            "args": args,
                            "id": call_id,
                        }

            else:
                yield {"type": "unknown", "raw": data}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_setup_message(self) -> dict:
        """Construct the Gemini BidiGenerateContent setup payload.

        All audio and VAD parameters come from centralized Settings.
        """
        s = self._settings

        setup_config = {
            "model": s.gemini_model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": s.gemini_voice_name,
                        }
                    }
                },
                "thinkingConfig": {
                    "thinkingLevel": s.gemini_thinking_level,
                },
            },
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "disabled": False,
                    "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                    "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                    "prefixPaddingMs": s.vad_prefix_padding_ms,
                    "silenceDurationMs": s.vad_silence_duration_ms,
                },
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
                "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
            },
            "systemInstruction": {
                "parts": [{"text": self._system_instruction.strip()}]
            },
        }

        if self._enable_transcription:
            setup_config["inputAudioTranscription"] = {}
            setup_config["outputAudioTranscription"] = {}

        # Include declarations of tools in setup root if provided
        if self._tools:
            setup_config["tools"] = self._tools

        return {"setup": setup_config}

    async def _send_setup(self) -> None:
        """Send the setup message to Gemini immediately after connecting."""
        setup = self._build_setup_message()
        await self._ws.send(json.dumps(setup))
        logger.debug("Gemini setup message sent.")
