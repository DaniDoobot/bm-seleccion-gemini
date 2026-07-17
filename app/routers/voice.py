"""Voice WebSocket router — Twilio ↔ Gemini Live audio bridge.

This router implements the minimal WebSocket endpoint required to test the
audio transport between Twilio Media Streams and Gemini Live in Phase 1.

What this router handles:
  1. Receive the Twilio "start" event to obtain stream_sid.
  2. Resolve the scenario from the ?scenario= query parameter.
  3. Open a Gemini Live session with the scenario's system instruction.
  4. Run two concurrent loops:
       twilio_to_gemini: forward Twilio µ-law audio → Gemini PCM 16 kHz.
       gemini_to_twilio: forward Gemini PCM 24 kHz → Twilio µ-law.
  5. Handle barge-in: clear Twilio audio buffer on Gemini "interrupted" event.
  6. Close and clean up both connections on exit.

What this router does NOT handle in Phase 1:
  - TwiML / IVR / incoming-call webhooks.
  - Twilio signature validation.
  - DTMF or candidate codes.
  - Tool / function calling.
  - Call recording.
  - Session persistence.
  - Post-call logic.
"""
import asyncio
import datetime
import json
import logging

import websockets.exceptions
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, Response, HTTPException, Request

from app.config import get_settings
from app.core.audio import decode_twilio_to_gemini, encode_gemini_to_twilio
from app.core.gemini_session import GeminiVoiceSession, OnboardingPhase
from app.scenarios.registry import get_scenario

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice"])


async def handle_voice_stream(websocket: WebSocket, scenario_id: str) -> None:
    """Core logic to run the Twilio ↔ Gemini Live audio streaming bridge."""
    await websocket.accept()

    # ── 1. Resolve scenario ───────────────────────────────────────────────────
    scenario_config = get_scenario(scenario_id)
    if scenario_config is None:
        logger.warning("Unknown scenario requested: %s", scenario_id)
        await websocket.close(code=1008, reason=f"Unknown scenario: '{scenario_id}'")
        return

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY is not configured.")
        await websocket.close(code=1008, reason="Service not configured.")
        return

    logger.info(
        "Voice stream connected. scenario=%s model=%s voice=%s",
        scenario_id,
        settings.gemini_model,
        settings.gemini_voice_name,
    )

    # ── 2. Wait for Twilio 'start' event ─────────────────────────────────────
    stream_sid: str = ""
    call_sid: str = ""
    account_sid: str = ""
    try:
        async def _wait_for_start() -> None:
            nonlocal stream_sid, call_sid, account_sid
            async for message in websocket.iter_text():
                data = json.loads(message)
                event = data.get("event")
                if event == "connected":
                    logger.debug("Twilio 'connected' event received.")
                    continue
                if event == "start":
                    start_data = data.get("start", {})
                    stream_sid = start_data.get("streamSid", "")
                    call_sid = start_data.get("callSid", "")
                    account_sid = start_data.get("accountSid", "")
                    logger.info(
                        "Twilio stream started. stream_sid=%s, call_sid=%s", stream_sid, call_sid
                    )
                    return
                logger.debug("Ignoring pre-start Twilio event: %s", event)

        await asyncio.wait_for(_wait_for_start(), timeout=10.0)

    except asyncio.TimeoutError:
        logger.error("Timed out waiting for Twilio 'start' event.")
        await websocket.close()
        return
    except Exception as exc:
        logger.error("Error receiving Twilio start event: %s", exc)
        await websocket.close()
        return

    # ── 3. Open Gemini session and run audio bridge ───────────────────────────
    gemini_session = GeminiVoiceSession(
        settings=settings,
        system_instruction=scenario_config.system_instruction,
        tools=scenario_config.tools,
        roleplay_transition_phrase=scenario_config.roleplay_transition_phrase,
        roleplay_initial_phrase=scenario_config.roleplay_initial_phrase,
        completion_phrase=scenario_config.completion_phrase,
    )

    # Store integration variables in session
    gemini_session.call_sid = call_sid
    gemini_session.stream_sid = stream_sid
    gemini_session.twilio_account_sid = account_sid
    gemini_session.scenario_id = scenario_config.scenario_id
    gemini_session._started_at_dt = datetime.datetime.now(datetime.timezone.utc)

    # Register scenario tool handlers
    from app.scenarios.registry import get_scenario_handlers
    handlers = get_scenario_handlers(scenario_config.scenario_id, gemini_session)
    for name, handler in handlers.items():
        gemini_session.register_tool_handler(name, handler)

    try:
        await gemini_session.connect()
    except ValueError as exc:
        logger.error("Gemini session configuration error: %s", exc)
        await websocket.close(code=1008, reason=str(exc))
        return
    except websockets.exceptions.WebSocketException as exc:
        logger.error("Failed to connect to Gemini Live: %s", exc)
        await websocket.close()
        return

    session_completed_sent = False
    was_failed = False

    async def send_session_completed_event():
        nonlocal session_completed_sent
        if session_completed_sent:
            return
        session_completed_sent = True

        rgpd_ok = gemini_session.candidate_context.get("rgpd_ok") is True
        if not rgpd_ok:
            logger.info("RGPD consent was not given. Skipping session.completed event dispatch.")
            return

        candidate_data = {
            "name": gemini_session.candidate_context.get("caller_user_name"),
            "lastname": gemini_session.candidate_context.get("caller_user_lastname"),
            "rgpd_ok": True
        }

        if was_failed:
            status = "failed"
            completion_reason = "gemini_error"
        else:
            is_finished = gemini_session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED
            status = "completed" if is_finished else "disconnected"
            completion_reason = "roleplay_finished" if is_finished else "caller_disconnected"

        started_at_dt = getattr(gemini_session, "_started_at_dt", None)
        if started_at_dt:
            finished_at_dt = datetime.datetime.now(datetime.timezone.utc)
            duration_seconds = int((finished_at_dt - started_at_dt).total_seconds())
            started_at_str = started_at_dt.isoformat()
            finished_at_str = finished_at_dt.isoformat()
        else:
            started_at_str = ""
            finished_at_str = ""
            duration_seconds = 0

        session_data = {
            "status": status,
            "roleplay_finished": gemini_session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED,
            "completion_reason": completion_reason,
            "started_at": started_at_str,
            "finished_at": finished_at_str,
            "duration_seconds": duration_seconds
        }

        recording_data = {
            "started": gemini_session.recording_started,
            "recording_sid": gemini_session.recording_sid or "",
            "status": "in_progress" if gemini_session.recording_started else "none"
        }

        event_payload = {
            "type": "Transcript",
            "event_type": "session.completed",
            "event_id": f"session.completed:{gemini_session.call_sid}",
            "data": {
                "agent_id": scenario_config.evaluation_agent_id,
                "conversation_id": gemini_session.call_sid or "",
                "call_sid": gemini_session.call_sid or "",
                "stream_sid": gemini_session.stream_sid or "",
                "scenario_id": scenario_config.scenario_id,
                "external_scenario_id": scenario_config.external_scenario_id,
                "candidate": candidate_data,
                "session": session_data,
                "recording": recording_data,
                "transcript": gemini_session.transcript
            }
        }

        from app.services.n8n_events import send_n8n_event
        idempotency_key = f"session.completed:{gemini_session.call_sid}"
        
        async def _dispatch_to_n8n():
            try:
                await send_n8n_event("Transcript", event_payload, idempotency_key)
            except Exception as e:
                logger.error("Error sending transcript event to n8n: %s", e)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_dispatch_to_n8n())
        except RuntimeError:
            asyncio.run(_dispatch_to_n8n())

    try:
        # Resampler states (threaded across consecutive audio chunks)
        twilio_rate_state = None
        gemini_rate_state = None
        gemini_ready = False

        # ── Loop A: Twilio → Gemini ───────────────────────────────────────────
        async def twilio_to_gemini_loop() -> None:
            nonlocal twilio_rate_state, gemini_ready
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "media":
                        if not gemini_ready:
                            # Discard audio until Gemini signals setup complete
                            continue
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            pcm_16k_b64, twilio_rate_state = decode_twilio_to_gemini(
                                payload, twilio_rate_state
                            )
                            if pcm_16k_b64:
                                await gemini_session.send_audio(pcm_16k_b64)

                    elif event == "stop":
                        logger.info("Twilio stream stopped.")
                        break

            except WebSocketDisconnect:
                logger.info("Twilio WebSocket disconnected.")
            except Exception as exc:
                logger.error("Error in twilio_to_gemini_loop: %s", exc)

        # ── Loop B: Gemini → Twilio ───────────────────────────────────────────
        async def gemini_to_twilio_loop() -> None:
            nonlocal gemini_rate_state, gemini_ready
            try:
                async for event_data in gemini_session.receive():
                    event_type = event_data["type"]

                    if event_type == "setup_complete":
                        gemini_ready = True
                        logger.info(
                            "Gemini ready. scenario=%s", scenario_config.scenario_id
                        )
                        if scenario_config.initial_message:
                            await gemini_session.send_text_turn(
                                f'Di únicamente, palabra por palabra, sin preámbulos ni comentarios adicionales: "{scenario_config.initial_message}"'
                            )

                    elif event_type == "audio":
                        mulaw_b64, gemini_rate_state = encode_gemini_to_twilio(
                            event_data["data"], gemini_rate_state
                        )
                        if mulaw_b64 and stream_sid:
                            media_msg = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": mulaw_b64},
                            }
                            await websocket.send_text(json.dumps(media_msg))

                    elif event_type == "interrupted":
                        # Barge-in: clear Twilio's audio playback buffer
                        if stream_sid:
                            logger.info(
                                "Barge-in detected. Clearing Twilio buffer. stream_sid=%s",
                                stream_sid,
                            )
                            clear_msg = {
                                "event": "clear",
                                "streamSid": stream_sid,
                            }
                            await websocket.send_text(json.dumps(clear_msg))

                    elif event_type == "turn_complete":
                        # Check if roleplay is finished. If so, exit loop to close connection cleanly.
                        if gemini_session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED:
                            logger.info(
                                "Roleplay reached finished phase. Closing stream. scenario=%s",
                                scenario_config.scenario_id,
                            )
                            break

                    elif event_type == "unknown":
                        logger.debug(
                            "Unhandled Gemini event: %s", event_data.get("raw")
                        )

            except websockets.exceptions.WebSocketException as exc:
                logger.error("Gemini WebSocket error: %s", exc)
            except Exception as exc:
                logger.error("Error in gemini_to_twilio_loop: %s", exc)

        # ── Run both loops concurrently ───────────────────────────────────────
        tw_task = asyncio.create_task(twilio_to_gemini_loop())
        gem_task = asyncio.create_task(gemini_to_twilio_loop())

        done, pending = await asyncio.wait(
            [tw_task, gem_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as exc:
        logger.error("Unexpected error in voice_stream: %s", exc)
        was_failed = True

    finally:
        try:
            await send_session_completed_event()
        except Exception as exc:
            logger.error("Failed to send session completed event: %s", exc)

        await gemini_session.close()
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info(
            "Voice stream closed. scenario=%s stream_sid=%s",
            scenario_config.scenario_id,
            stream_sid,
        )


@router.websocket("/voice/stream")
async def voice_stream(
    websocket: WebSocket,
    scenario: str = Query(
        ...,
        description="Scenario slug (e.g. seleccion_1, seleccion_2).",
    ),
) -> None:
    """Legacy WebSocket endpoint using query parameter 'scenario'."""
    await handle_voice_stream(websocket, scenario)


@router.websocket("/voice/stream/{scenario_id}")
async def voice_stream_path(
    websocket: WebSocket,
    scenario_id: str,
) -> None:
    """WebSocket endpoint using path parameter 'scenario_id'."""
    await handle_voice_stream(websocket, scenario_id)


@router.api_route(
    "/voice/incoming/{scenario_id}",
    methods=["GET", "POST"],
    summary="Twilio incoming call webhook",
)
async def voice_incoming(scenario_id: str) -> Response:
    """TwiML response to connect Twilio call to the scenario's WebSocket stream."""
    scenario_config = get_scenario(scenario_id)
    if scenario_config is None:
        logger.warning("Incoming call request for unknown scenario: %s", scenario_id)
        raise HTTPException(status_code=404, detail="Scenario not found")

    settings = get_settings()
    if not settings.public_ws_base_url:
        logger.error("PUBLIC_WS_BASE_URL is not configured. Cannot generate TwiML XML.")
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_WS_BASE_URL is not configured.",
        )

    # Construct public WebSocket URL for the scenario
    ws_base = settings.public_ws_base_url.rstrip("/")
    ws_url = f"{ws_base}/{scenario_id}"

    # Build TwiML XML response
    twiml_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
    <Hangup />
</Response>"""

    return Response(content=twiml_xml, media_type="application/xml")


@router.post("/voice/recording-status/{scenario_id}")
async def recording_status_callback(
    scenario_id: str,
    request: Request,
) -> Response:
    """Twilio callback received when call recording is completed or absent."""
    settings = get_settings()

    # 1. Resolve scenario first
    scenario_config = get_scenario(scenario_id)
    if scenario_config is None:
        logger.warning("Recording status callback for unknown scenario: %s", scenario_id)
        raise HTTPException(status_code=404, detail="Scenario not found")

    # 2. Twilio Signature Validation
    signature = request.headers.get("X-Twilio-Signature")
    form_data = await request.form()
    params = dict(form_data)

    if not settings.twilio_auth_token:
        logger.error("TWILIO_AUTH_TOKEN is not configured. Cannot validate signature.")
        raise HTTPException(status_code=403, detail="Twilio auth token not configured")

    if not settings.public_http_base_url:
        logger.error("PUBLIC_HTTP_BASE_URL is not configured. Cannot validate signature URL.")
        raise HTTPException(status_code=403, detail="Public HTTP base URL not configured")

    url = f"{settings.public_http_base_url.rstrip('/')}/voice/recording-status/{scenario_id}"

    from twilio.request_validator import RequestValidator
    validator = RequestValidator(settings.twilio_auth_token)
    if not validator.validate(url, params, signature):
        logger.warning("Twilio signature validation failed for recording callback.")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Signature is valid! Return 204 immediately and dispatch event in background
    recording_status = params.get("RecordingStatus")
    call_sid = params.get("CallSid")
    recording_sid = params.get("RecordingSid")

    logger.info(
        "Twilio recording callback signature validated. CallSid=%s, RecordingSid=%s, Status=%s",
        call_sid,
        recording_sid,
        recording_status
    )

    async def _dispatch_recording_event():
        try:
            from app.services.n8n_events import send_n8n_event
            
            if recording_status == "absent":
                event_type = "recording.absent"
                idempotency_key = f"recording.absent:{recording_sid}"
                payload = {
                    "type": "Audio",
                    "event_type": event_type,
                    "event_id": f"{event_type}:{recording_sid}",
                    "data": {
                        "agent_id": scenario_config.evaluation_agent_id,
                        "conversation_id": call_sid,
                        "call_sid": call_sid,
                        "scenario_id": scenario_id,
                        "external_scenario_id": scenario_config.external_scenario_id,
                        "recording": {
                            "recording_sid": recording_sid,
                            "status": "absent"
                        }
                    }
                }
            else:
                event_type = "recording.completed"
                idempotency_key = f"recording.completed:{recording_sid}"
                
                # Safe casting duration
                try:
                    duration = int(float(params.get("RecordingDuration", 0)))
                except (ValueError, TypeError):
                    duration = 0
                    
                try:
                    channels = int(params.get("RecordingChannels", 2))
                except (ValueError, TypeError):
                    channels = 2

                payload = {
                    "type": "Audio",
                    "event_type": event_type,
                    "event_id": f"{event_type}:{recording_sid}",
                    "data": {
                        "agent_id": scenario_config.evaluation_agent_id,
                        "conversation_id": call_sid,
                        "call_sid": call_sid,
                        "scenario_id": scenario_id,
                        "external_scenario_id": scenario_config.external_scenario_id,
                        "recording": {
                            "recording_sid": recording_sid,
                            "status": "completed",
                            "url": params.get("RecordingUrl"),
                            "duration_seconds": duration,
                            "channels": channels,
                            "track": params.get("RecordingTrack", "both"),
                            "started_at": params.get("RecordingStartTime"),
                            "source": params.get("RecordingSource", "StartCallRecordingAPI")
                        }
                    }
                }
                
            await send_n8n_event(event_type, payload, idempotency_key)
            
        except Exception as e:
            logger.error("Error dispatching recording event to n8n: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_dispatch_recording_event())
    except RuntimeError:
        asyncio.run(_dispatch_recording_event())

    return Response(status_code=204)

