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
import json
import logging

import websockets.exceptions
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.audio import decode_twilio_to_gemini, encode_gemini_to_twilio
from app.core.gemini_session import GeminiVoiceSession
from app.scenarios.registry import get_scenario

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice"])


@router.websocket("/voice/stream")
async def voice_stream(
    websocket: WebSocket,
    scenario: str = Query(
        ...,
        description="Scenario slug (e.g. seleccion_1, seleccion_2).",
    ),
) -> None:
    """WebSocket endpoint for Twilio ↔ Gemini Live audio streaming.

    The client (Twilio) connects and sends a 'start' event followed by
    'media' events containing base64-encoded µ-law 8 kHz audio chunks.

    Query parameters:
        scenario: Slug of the voice scenario to run.

    The WebSocket closes with code 1008 (Policy Violation) if:
      - The scenario slug is not registered.
      - GEMINI_API_KEY is not configured.
    """
    await websocket.accept()

    # ── 1. Resolve scenario ───────────────────────────────────────────────────
    scenario_config = get_scenario(scenario)
    if scenario_config is None:
        logger.warning("Unknown scenario requested: %s", scenario)
        await websocket.close(code=1008, reason=f"Unknown scenario: '{scenario}'")
        return

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY is not configured.")
        await websocket.close(code=1008, reason="Service not configured.")
        return

    logger.info(
        "Voice stream connected. scenario=%s model=%s voice=%s",
        scenario,
        settings.gemini_model,
        settings.gemini_voice_name,
    )

    # ── 2. Wait for Twilio 'start' event ─────────────────────────────────────
    stream_sid: str = ""
    try:
        async def _wait_for_start() -> None:
            nonlocal stream_sid
            async for message in websocket.iter_text():
                data = json.loads(message)
                event = data.get("event")
                if event == "connected":
                    logger.debug("Twilio 'connected' event received.")
                    continue
                if event == "start":
                    start_data = data.get("start", {})
                    stream_sid = start_data.get("streamSid", "")
                    logger.info(
                        "Twilio stream started. stream_sid=%s", stream_sid
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

    finally:
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
