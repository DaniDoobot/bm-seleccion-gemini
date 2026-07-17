import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import datetime
import xml.etree.ElementTree as ET

from app.main import app
from app.config import Settings
from app.core.gemini_session import GeminiVoiceSession, OnboardingPhase
from app.scenarios.registry import get_scenario
from app.scenarios.seleccion_1 import SELECCION_1_CONFIG
from app.scenarios.seleccion_2 import SELECCION_2_CONFIG
from app.services.twilio_recording import start_twilio_recording
from app.services.n8n_events import send_n8n_event

client = TestClient(app)


# 1. Mappings & Config Tests
def test_scenario_evaluation_agent_ids():
    """Verify selection_1 and selection_2 use the correct evaluation_agent_id."""
    assert SELECCION_1_CONFIG.evaluation_agent_id == "agent_3801kjcvpbseek68yv7hypkyhb7z"
    assert SELECCION_2_CONFIG.evaluation_agent_id == "agent_1401kjcgdv7reg7t41ak0vbt53e5"
    assert SELECCION_1_CONFIG.external_scenario_id == "bm_atp_muro_doctor"
    assert SELECCION_2_CONFIG.external_scenario_id == "bm_atp_retraso_envio_baja"


# 2. Transcription Setup configuration
def test_transcription_enabled_in_setup_config():
    """Verify inputAudioTranscription and outputAudioTranscription are present if enabled."""
    settings = Settings(gemini_transcription_enabled=True, env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    setup = session._build_setup_message()
    assert "inputAudioTranscription" in setup["setup"]
    assert "outputAudioTranscription" in setup["setup"]


def test_transcription_disabled_in_setup_config():
    """Verify inputAudioTranscription and outputAudioTranscription are absent if disabled."""
    settings = Settings(gemini_transcription_enabled=False, env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    setup = session._build_setup_message()
    assert "inputAudioTranscription" not in setup["setup"]
    assert "outputAudioTranscription" not in setup["setup"]


# 3. Turn Consolidation & Speaker/Phase calculation
def test_consolidate_partial_transcripts():
    """Verify partial speech fragments consolidate into turns properly."""
    settings = Settings(gemini_transcription_enabled=True, env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    # 1. Starts in onboarding phase
    assert session.onboarding_phase == OnboardingPhase.WAITING_READY
    
    # 2. Candidate speaks in chunks
    session._user_transcript_accumulator += " Hola"
    session._user_transcript_accumulator += " señor,"
    session._user_transcript_accumulator += " buenas."
    
    # 3. Gemini starts speaking (yields modelTurn)
    # Simulate what receive loop does:
    user_text = session._user_transcript_accumulator.strip()
    if user_text:
        session.add_transcript_turn("candidate", user_text)
    session._user_transcript_accumulator = ""
    
    assert len(session.transcript) == 1
    turn = session.transcript[0]
    assert turn["sequence"] == 1
    assert turn["speaker"] == "candidate"
    assert turn["phase"] == "onboarding"
    assert turn["text"] == "Hola señor, buenas."
    assert "timestamp" in turn
    
    # 4. Patient speaks in chunks
    session._model_transcript_accumulator += " Hola,"
    session._model_transcript_accumulator += " quiero"
    session._model_transcript_accumulator += " hablar con el doctor."
    
    # 5. Turn completes
    model_text = session._model_transcript_accumulator.strip()
    if model_text:
        session.add_transcript_turn("patient", model_text)
    session._model_transcript_accumulator = ""
    
    assert len(session.transcript) == 2
    turn2 = session.transcript[1]
    assert turn2["sequence"] == 2
    assert turn2["speaker"] == "patient"
    assert turn2["phase"] == "onboarding"
    assert turn2["text"] == "Hola, quiero hablar con el doctor."


def test_turn_phase_calculates_correctly():
    """Verify phase resolves to roleplay only when roleplay is active or finished."""
    settings = Settings(env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    session.onboarding_phase = OnboardingPhase.WAITING_READY
    session.add_transcript_turn("candidate", "Onboarding text")
    assert session.transcript[0]["phase"] == "onboarding"
    
    session.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE
    session.add_transcript_turn("patient", "Roleplay text")
    assert session.transcript[1]["phase"] == "roleplay"
    
    session.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
    session.add_transcript_turn("candidate", "Closure text")
    assert session.transcript[2]["phase"] == "roleplay"


def test_turn_accumulation_filters_empty_texts():
    """Verify empty text turns are not recorded."""
    settings = Settings(env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    session.add_transcript_turn("candidate", "   ")
    assert len(session.transcript) == 0


# 4. Recording triggering logic
@patch("app.services.twilio_recording.Client")
def test_start_twilio_recording_sdk_call(mock_twilio_client_class):
    """Verify start_twilio_recording makes correct Twilio SDK calls with dual/both config."""
    mock_client = MagicMock()
    mock_twilio_client_class.return_value = mock_client
    
    mock_recording = MagicMock()
    mock_recording.sid = "RE123456"
    mock_client.calls.return_value.recordings.create.return_value = mock_recording
    
    settings = Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="AUTH123",
        public_http_base_url="https://test-host.com",
        call_recording_enabled=True,
        env_file=None
    )
    
    with patch("app.services.twilio_recording.get_settings", return_value=settings):
        rec_sid = start_twilio_recording("CA999", "seleccion_1")
        assert rec_sid == "RE123456"
        
        mock_client.calls.assert_called_once_with("CA999")
        mock_client.calls.return_value.recordings.create.assert_called_once_with(
            recording_channels="dual",
            recording_track="both",
            recording_status_callback="https://test-host.com/voice/recording-status/seleccion_1",
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed", "absent"],
            trim="do-not-trim"
        )


@patch("app.services.twilio_recording.Client")
def test_recording_failure_does_not_break_call(mock_twilio_client_class):
    """Verify that start_twilio_recording returns None on failure and doesn't crash."""
    mock_twilio_client_class.side_effect = Exception("Twilio API is down")
    settings = Settings(
        twilio_account_sid="AC123",
        twilio_auth_token="AUTH123",
        public_http_base_url="https://test-host.com",
        call_recording_enabled=True,
        env_file=None
    )
    with patch("app.services.twilio_recording.get_settings", return_value=settings):
        rec_sid = start_twilio_recording("CA999", "seleccion_1")
        assert rec_sid is None  # Fails gracefully


# 5. n8n Client Retry Policy
@patch("httpx.AsyncClient.post")
@pytest.mark.asyncio
async def test_send_n8n_event_does_not_retry_4xx(mock_post):
    """Verify send_n8n_event does not retry on 4xx responses."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_post.return_value = mock_response
    
    settings = Settings(
        n8n_events_webhook_url="https://n8n.com/webhook",
        n8n_webhook_token="token",
        env_file=None
    )
    
    with patch("app.services.n8n_events.get_settings", return_value=settings):
        res = await send_n8n_event("post_call_transcription", {"data": "test"}, "key")
        assert res is False
        assert mock_post.call_count == 1  # No retries on 4xx!


@patch("httpx.AsyncClient.post")
@pytest.mark.asyncio
async def test_send_n8n_event_retries_on_5xx_and_network_errors(mock_post):
    """Verify send_n8n_event retries up to 3 times on 5xx or network errors."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_post.return_value = mock_response
    
    settings = Settings(
        n8n_events_webhook_url="https://n8n.com/webhook",
        n8n_webhook_token="token",
        env_file=None
    )
    
    with patch("app.services.n8n_events.get_settings", return_value=settings):
        # Patch sleep to make test run fast
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res = await send_n8n_event("post_call_transcription", {"data": "test"}, "key")
            assert res is False
            assert mock_post.call_count == 3  # 3 attempts!


@patch("httpx.AsyncClient.post")
@pytest.mark.asyncio
async def test_send_n8n_event_with_token(mock_post):
    """Verify send_n8n_event includes Authorization header when token is set."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response
    
    settings = Settings(
        n8n_events_webhook_url="https://n8n.com/webhook",
        n8n_webhook_token="test-secret-token",
        env_file=None
    )
    
    with patch("app.services.n8n_events.get_settings", return_value=settings):
        res = await send_n8n_event("post_call_transcription", {"data": "test"}, "key")
        assert res is True
        mock_post.assert_called_once()
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-secret-token"
        assert headers["Content-Type"] == "application/json"
        assert headers["Idempotency-Key"] == "key"


@patch("httpx.AsyncClient.post")
@pytest.mark.asyncio
async def test_send_n8n_event_without_token(mock_post):
    """Verify send_n8n_event does not include Authorization header when token is empty."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post.return_value = mock_response
    
    settings = Settings(
        n8n_events_webhook_url="https://n8n.com/webhook",
        n8n_webhook_token="",
        env_file=None
    )
    
    with patch("app.services.n8n_events.get_settings", return_value=settings):
        res = await send_n8n_event("post_call_transcription", {"data": "test"}, "key")
        assert res is True
        mock_post.assert_called_once()
        headers = mock_post.call_args[1]["headers"]
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"
        assert headers["Idempotency-Key"] == "key"



# 6. Incoming TwiML & Webhook Callback Signature tests
def test_recording_status_callback_403_on_invalid_signature(monkeypatch):
    """Verify signature validator blocks invalid request with 403."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            twilio_auth_token="fake-token",
            public_http_base_url="https://test-host.com",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    # Send request with bad signature header
    headers = {"X-Twilio-Signature": "invalid-signature"}
    response = client.post("/voice/recording-status/seleccion_1", headers=headers, data={
        "CallSid": "CA111",
        "RecordingSid": "RE222",
        "RecordingStatus": "completed"
    })
    
    assert response.status_code == 403


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_recording_status_callback_204_on_valid_signature(mock_send_event, mock_validate, monkeypatch):
    """Verify callback validates signature, returns 204, and triggers n8n event."""
    mock_validate.return_value = True
    mock_send_event.return_value = True
    
    from app.services.call_consent_registry import registry
    registry.remove("CA111")
    entry = registry.get_or_create("CA111", "seleccion_1")
    entry.consent_status = "accepted"
    
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            twilio_auth_token="fake-token",
            public_http_base_url="https://test-host.com",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    headers = {"X-Twilio-Signature": "valid-signature"}
    response = client.post("/voice/recording-status/seleccion_1", headers=headers, data={
        "CallSid": "CA111",
        "RecordingSid": "RE222",
        "RecordingStatus": "completed",
        "RecordingUrl": "https://api.twilio.com/recordings/RE222",
        "RecordingDuration": "15.5",
        "RecordingChannels": "2",
        "RecordingTrack": "both",
        "RecordingStartTime": "2026-07-17T08:30:00Z",
        "RecordingSource": "StartCallRecordingAPI"
    })
    
    assert response.status_code == 204
    
    # Allow background task to execute
    import time
    time.sleep(0.1)
    
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert event_type == "recording.completed"
    assert idempotency_key == "recording.completed:RE222"
    assert payload["type"] == "post_call_audio"
    assert payload["event_type"] == "recording.completed"
    assert "body" not in payload  # Verify no root body wrapper!
    
    data = payload["data"]
    assert data["agent_id"] == "agent_3801kjcvpbseek68yv7hypkyhb7z"
    assert data["call_sid"] == "CA111"
    assert data["recording"]["recording_sid"] == "RE222"
    assert data["recording"]["status"] == "completed"
    assert data["recording"]["duration_seconds"] == 15
    assert data["recording"]["channels"] == 2


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_recording_status_callback_absent(mock_send_event, mock_validate, monkeypatch):
    """Verify callback processes 'absent' status correctly."""
    mock_validate.return_value = True
    mock_send_event.return_value = True
    
    from app.services.call_consent_registry import registry
    registry.remove("CA111")
    entry = registry.get_or_create("CA111", "seleccion_2")
    entry.consent_status = "accepted"
    
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            twilio_auth_token="fake-token",
            public_http_base_url="https://test-host.com",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    headers = {"X-Twilio-Signature": "valid-signature"}
    response = client.post("/voice/recording-status/seleccion_2", headers=headers, data={
        "CallSid": "CA111",
        "RecordingSid": "RE222",
        "RecordingStatus": "absent"
    })
    
    assert response.status_code == 204
    
    # Allow background task to execute
    import time
    time.sleep(0.1)
    
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert event_type == "recording.absent"
    assert idempotency_key == "recording.absent:RE222"
    assert payload["type"] == "post_call_audio"
    assert payload["event_type"] == "recording.absent"
    
    data = payload["data"]
    assert data["agent_id"] == "agent_1401kjcgdv7reg7t41ak0vbt53e5"
    assert data["recording"]["recording_sid"] == "RE222"
    assert data["recording"]["status"] == "absent"


# 7. WebSocket start capture, context validation & RGPD requirements
@patch("app.routers.voice.GeminiVoiceSession")
def test_websocket_stream_captures_ids_but_does_not_start_recording(mock_session_class, monkeypatch):
    """Verify WebSocket captures CallSid on start and sets requested status."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    import asyncio
    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = False
    mock_session_inst.recording_start_attempted = False
    mock_session_inst.candidate_context = {"saved": False, "rgpd_ok": None}


    async def mock_receive():
        yield {"type": "setup_complete"}
        # Loop forever or sleep to keep it alive
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    mock_session_inst.receive = mock_receive
    mock_session_class.return_value = mock_session_inst

    with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
        websocket.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        websocket.send_json({
            "event": "start",
            "start": {
                "accountSid": "AC_TEST",
                "streamSid": "MZ_TEST",
                "callSid": "CA_TEST"
            }
        })
        # Wait a bit
        import time
        time.sleep(0.1)
        
    # Verify values were set on gemini_session instance
    assert mock_session_inst.call_sid == "CA_TEST"
    assert mock_session_inst.stream_sid == "MZ_TEST"
    assert mock_session_inst.twilio_account_sid == "AC_TEST"
    assert mock_session_inst.scenario_id == "seleccion_1"
    
    # Recording was set as requested from the start
    assert mock_session_inst.recording_started is True
    assert mock_session_inst.recording_start_attempted is True
    assert mock_session_inst.recording_status == "requested"


# 8. conversation_id correlation and RGPD normalization tests
from app.scenarios.common import make_save_candidate_context_handler

def test_rgpd_normalization_acceptance():
    """Verify that valid RGPD acceptances (Sí, Si, true, True) normalize to True."""
    for val in ["Sí", "Si", "true", "True", True, "  Si  ", "sí"]:
        session = MagicMock()
        session.candidate_context = {"saved": False, "rgpd_ok": None}
        session.onboarding_phase = OnboardingPhase.READY_TO_SAVE
        session.call_sid = "CA123"
        session.recording_start_attempted = False
        
        handler = make_save_candidate_context_handler(session, "seleccion_1")
        res = handler({
            "caller_user_name": "Daniel",
            "caller_user_lastname": "Martínez",
            "rgpd_ok": val,
            "scenario": "seleccion_1"
        })
        assert res["success"] is True
        assert session.candidate_context["rgpd_ok"] is True


def test_rgpd_normalization_rejection():
    """Verify that rejection or invalid RGPD values do not save and raise ValueError."""
    for val in ["No", "False", False, "ninguno", ""]:
        session = MagicMock()
        session.candidate_context = {"saved": False, "rgpd_ok": None}
        session.onboarding_phase = OnboardingPhase.READY_TO_SAVE
        
        handler = make_save_candidate_context_handler(session, "seleccion_1")
        with pytest.raises(ValueError):
            handler({
                "caller_user_name": "Daniel",
                "caller_user_lastname": "Martínez",
                "rgpd_ok": val,
                "scenario": "seleccion_1"
            })
        assert session.candidate_context.get("saved") is not True


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_conversation_id_correlation_in_audio_event(mock_send_event, mock_validate, monkeypatch):
    """Verify that the Audio event contains conversation_id equal to call_sid."""
    mock_validate.return_value = True
    mock_send_event.return_value = True
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_CORRELATION_123")
    entry = registry.get_or_create("CA_CORRELATION_123", "seleccion_1")
    entry.consent_status = "accepted"
    
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            twilio_auth_token="fake-token",
            public_http_base_url="https://test-host.com",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    headers = {"X-Twilio-Signature": "valid-signature"}
    response = client.post("/voice/recording-status/seleccion_1", headers=headers, data={
        "CallSid": "CA_CORRELATION_123",
        "RecordingSid": "RE_CORRELATION_123",
        "RecordingStatus": "completed",
        "RecordingUrl": "https://api.twilio.com/recordings/RE_CORRELATION_123"
    })
    
    assert response.status_code == 204
    import time
    time.sleep(0.1)
    
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert payload["type"] == "post_call_audio"
    assert payload["data"]["conversation_id"] == "CA_CORRELATION_123"
    assert payload["data"]["call_sid"] == "CA_CORRELATION_123"


@patch("app.routers.voice.GeminiVoiceSession")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_conversation_id_correlation_in_transcript_event(mock_send_event, mock_session_class, monkeypatch):
    """Verify that when WebSocket closes, session.completed Transcript event is dispatched with correct conversation_id."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = True
    mock_session_inst.recording_sid = "RE_MOCK"
    mock_session_inst.transcript = [{"sequence": 1, "speaker": "candidate", "text": "Hola", "phase": "onboarding"}]
    mock_session_inst.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
    mock_session_inst.candidate_context = {
        "saved": True,
        "caller_user_name": "Daniel",
        "caller_user_lastname": "Martínez",
        "rgpd_ok": True
    }

    async def mock_receive():
        yield {"type": "setup_complete"}
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    mock_session_inst.receive = mock_receive
    mock_session_class.return_value = mock_session_inst

    with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
        websocket.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        websocket.send_json({
            "event": "start",
            "start": {
                "accountSid": "AC_TEST",
                "streamSid": "MZ_TEST",
                "callSid": "CA_TRANSCRIPT_123"
            }
        })
        import time
        time.sleep(0.1)
    
    time.sleep(0.1)
    
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert payload["type"] == "post_call_transcription"
    assert payload["data"]["conversation_id"] == "CA_TRANSCRIPT_123"
    assert payload["data"]["call_sid"] == "CA_TRANSCRIPT_123"
    assert payload["data"]["candidate"]["rgpd_ok"] is True


# 9. Transient reconnects & RGPD auto-commit tests
@pytest.mark.asyncio
async def test_rgpd_acceptance_auto_commits_context():
    """Verify that when user transcript contains explicit RGPD acceptance, the context is saved immediately and phase goes to CONTEXT_SAVED."""
    settings = Settings(env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    # Setup provisional info
    session.provisional_name = "Daniel"
    session.provisional_lastname = "Martínez"
    session.call_sid = "CA123"
    session.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE
    
    # Mock Twilio recording to verify it is NEVER called
    with patch("app.services.twilio_recording.start_twilio_recording") as mock_start_rec:
        # Explicit acceptance
        session.process_user_transcript("Sí, acepto el consentimiento y la grabación.")
        
        # Give event loop a chance to run background tasks
        await asyncio.sleep(0.1)
        
        assert session.candidate_context["saved"] is True
        assert session.candidate_context["rgpd_ok"] is True
        assert session.onboarding_phase == OnboardingPhase.CONTEXT_SAVED
        mock_start_rec.assert_not_called()


@pytest.mark.asyncio
async def test_subsequent_tool_call_returns_already_saved():
    """Verify that once auto-saved, calling the tool save_candidate_context returns status: already_saved without double starting the recording."""
    settings = Settings(env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    session.provisional_name = "Daniel"
    session.provisional_lastname = "Martínez"
    session.call_sid = "CA123"
    session.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE
    
    # Pre-set recording properties as if set by WebSocket start
    session.recording_started = True
    session.recording_start_attempted = True
    session.recording_status = "requested"
    
    with patch("app.services.twilio_recording.start_twilio_recording") as mock_record:
        session.process_user_transcript("Sí, acepto.")
        await asyncio.sleep(0.1)
        
        assert session.recording_started is True
        mock_record.assert_not_called()
        
        # Now run the tool handler
        handler = make_save_candidate_context_handler(session, "seleccion_1")
        res = handler({
            "caller_user_name": "Daniel",
            "caller_user_lastname": "Martínez",
            "rgpd_ok": "Si",
            "scenario": "seleccion_1"
        })
        
        assert res["success"] is False
        assert res["status"] == "already_saved"
        mock_record.assert_not_called()


@patch("app.routers.voice.GeminiVoiceSession")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_transient_reconnect_1011(mock_send_event, mock_session_class, monkeypatch):
    """Verify that a 1011 close code triggers a single reconnect attempt, preserves context, and doesn't repeat onboarding."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.reconnect = AsyncMock()
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = True
    mock_session_inst.onboarding_phase = OnboardingPhase.ROLEPLAY_ACTIVE
    mock_session_inst.candidate_context = {
        "saved": True,
        "caller_user_name": "Daniel",
        "caller_user_lastname": "Martínez",
        "rgpd_ok": True
    }
    mock_session_inst.transcript = []

    # First receive raises ConnectionClosed with code 1011
    async def mock_receive_first():
        yield {"type": "setup_complete"}
        # Raise websockets ConnectionClosed
        from websockets.exceptions import ConnectionClosed
        from websockets.frames import Close
        raise ConnectionClosed(Close(1011, "Internal Error"), None)

    mock_session_inst.receive = mock_receive_first
    mock_session_class.return_value = mock_session_inst

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
            websocket.send_json({"event": "connected"})
            websocket.send_json({
                "event": "start",
                "start": {
                    "accountSid": "AC_TEST",
                    "streamSid": "MZ_TEST",
                    "callSid": "CA_RECONNECT_123"
                }
            })
            import time
            time.sleep(0.2) # Allow connection and loops to run

    # Verify reconnect was called
    mock_session_inst.reconnect.assert_called_once()


@patch("app.routers.voice.GeminiVoiceSession")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_reconnect_failure_sends_session_completed_if_rgpd_accepted(mock_send_event, mock_session_class, monkeypatch):
    """Verify that if reconnect fails after RGPD accepted, session.completed is sent with status=failed and completion_reason=gemini_error."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    # Mock reconnect to raise exception
    mock_session_inst.reconnect.side_effect = Exception("Permanent connection drop")
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = True
    mock_session_inst.onboarding_phase = OnboardingPhase.CONTEXT_SAVED
    mock_session_inst.candidate_context = {
        "saved": True,
        "caller_user_name": "Daniel",
        "caller_user_lastname": "Martínez",
        "rgpd_ok": True
    }
    mock_session_inst.transcript = []

    async def mock_receive():
        yield {"type": "setup_complete"}
        from websockets.exceptions import ConnectionClosed
        from websockets.frames import Close
        raise ConnectionClosed(Close(1011, "Internal Error"), None)

    mock_session_inst.receive = mock_receive
    mock_session_class.return_value = mock_session_inst

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
            websocket.send_json({"event": "connected"})
            websocket.send_json({
                "event": "start",
                "start": {
                    "accountSid": "AC_TEST",
                    "streamSid": "MZ_TEST",
                    "callSid": "CA_RECONNECT_FAIL_123"
                }
            })
            import time
            time.sleep(0.2)

    time.sleep(0.1)
    
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert payload["type"] == "post_call_transcription"
    assert payload["data"]["session"]["status"] == "failed"
    assert payload["data"]["session"]["completion_reason"] == "gemini_error"


@patch("app.routers.voice.GeminiVoiceSession")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_reconnect_failure_does_not_send_n8n_if_no_consent(mock_send_event, mock_session_class, monkeypatch):
    """Verify that if reconnect fails before consent is given, no n8n session.completed is sent."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.reconnect.side_effect = Exception("Permanent connection drop")
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = False
    mock_session_inst.onboarding_phase = OnboardingPhase.WAITING_CANDIDATE_DATA
    mock_session_inst.candidate_context = {
        "saved": False,
        "caller_user_name": None,
        "caller_user_lastname": None,
        "rgpd_ok": None
    }
    mock_session_inst.transcript = []

    async def mock_receive():
        yield {"type": "setup_complete"}
        from websockets.exceptions import ConnectionClosed
        from websockets.frames import Close
        raise ConnectionClosed(Close(1011, "Internal Error"), None)

    mock_session_inst.receive = mock_receive
    mock_session_class.return_value = mock_session_inst

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
            websocket.send_json({"event": "connected"})
            websocket.send_json({
                "event": "start",
                "start": {
                    "accountSid": "AC_TEST",
                    "streamSid": "MZ_TEST",
                    "callSid": "CA_RECONNECT_FAIL_NOCONSENT"
                }
            })
            import time
            time.sleep(0.2)

    time.sleep(0.1)
    
    mock_send_event.assert_not_called()


# 10. Roleplay transitions, finish logic, clean closes, and log redactions tests
@pytest.mark.asyncio
async def test_roleplay_transitions_and_finished_states():
    """Verify that EXPLANATION goes to READY_TO_START_ROLEPLAY, then to ROLEPLAY_ACTIVE, that subsequent turns have phase='roleplay', and completion phrase transitions to ROLEPLAY_FINISHED."""
    settings = Settings(env_file=None)
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    session.provisional_name = "Daniel"
    session.provisional_lastname = "Martínez"
    session.call_sid = "CA123"
    
    session.onboarding_phase = OnboardingPhase.CONTEXT_SAVED
    
    # Model turn transitions to EXPLANATION
    session.process_model_transcript("Te explicaré el roleplay...")
    assert session.onboarding_phase == OnboardingPhase.EXPLANATION
    
    # Model turn asking if ready/clear transitions to READY_TO_START_ROLEPLAY
    session.process_model_transcript("¿Está todo claro para comenzar?")
    assert session.onboarding_phase == OnboardingPhase.READY_TO_START_ROLEPLAY
    
    # User confirming transitions to ROLEPLAY_ACTIVE
    session.process_user_transcript("Sí, de acuerdo, comencemos.")
    assert session.onboarding_phase == OnboardingPhase.ROLEPLAY_ACTIVE
    
    # Verify subsequent turn has phase = roleplay
    session.add_transcript_turn("candidate", "Hola doctor...")
    assert session.transcript[-1]["phase"] == "roleplay"
    
    # Model final phrase transitions to ROLEPLAY_FINISHED
    # Set model accumulator and process turnComplete check
    session._model_transcript_accumulator = "La prueba ha terminado. Gracias por participar."
    # Simulate turnComplete block
    has_terminado = "la prueba ha terminado" in session._normalize_text(session._model_transcript_accumulator)
    has_gracias = "gracias por participar" in session._normalize_text(session._model_transcript_accumulator)
    if (has_terminado and has_gracias) or ("la prueba ha terminado gracias por participar" in session._normalize_text(session._model_transcript_accumulator)):
        session.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
        
    assert session.onboarding_phase == OnboardingPhase.ROLEPLAY_FINISHED


@patch("app.routers.voice.GeminiVoiceSession")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_websocket_closes_cleanly_on_roleplay_finished_without_twilio_stop(mock_send_event, mock_session_class, monkeypatch):
    """Verify that when ROLEPLAY_FINISHED is reached, the websocket closes and session.completed event is sent with completed status, without requiring twilio_stop."""
    def mock_get_settings():
        return Settings(
            gemini_api_key="dummy-key",
            env_file=None
        )
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)

    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.close = AsyncMock()
    mock_session_inst.recording_started = True
    mock_session_inst.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
    mock_session_inst.candidate_context = {
        "saved": True,
        "caller_user_name": "Daniel",
        "caller_user_lastname": "Martínez",
        "rgpd_ok": True
    }
    mock_session_inst.transcript = []

    # Receive returns setup_complete, then turn_complete to trigger loop break
    async def mock_receive():
        yield {"type": "setup_complete"}
        yield {"type": "turn_complete"}
        # Loop will exit here because phase is ROLEPLAY_FINISHED
        await asyncio.sleep(3600)

    mock_session_inst.receive = mock_receive
    mock_session_class.return_value = mock_session_inst

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
            websocket.send_json({"event": "connected"})
            websocket.send_json({
                "event": "start",
                "start": {
                    "accountSid": "AC_TEST",
                    "streamSid": "MZ_TEST",
                    "callSid": "CA_FINISHED_123"
                }
            })
            import time
            time.sleep(0.2)

    # Verify session completed event was sent with status="completed"
    mock_send_event.assert_called_once()
    args, kwargs = mock_send_event.call_args
    event_type, payload, idempotency_key = args
    
    assert payload["type"] == "post_call_transcription"
    assert payload["data"]["session"]["status"] == "completed"
    assert payload["data"]["session"]["roleplay_finished"] is True
    assert payload["data"]["session"]["completion_reason"] == "roleplay_finished"


def test_log_transcripts_false_redacts_complete_texts(caplog):
    """Verify that with log_transcripts=False, process_user_transcript and process_model_transcript do not output the full text in INFO or DEBUG level logs."""
    import logging
    settings = Settings(env_file=None)
    settings.log_transcripts = False
    
    session = GeminiVoiceSession(settings=settings, system_instruction="instruction")
    
    with caplog.at_level(logging.INFO):
        session.process_user_transcript("Mi nombre es Daniel Martínez y acepto.")
        session.process_model_transcript("Hola doctor, soy el paciente.")
        
    for record in caplog.records:
        assert "Mi nombre es Daniel Martínez y acepto." not in record.message
        assert "Hola doctor, soy el paciente." not in record.message


# 11. Mandatory tests for TwiML-driven recording, CallConsentRegistry, and webhook event routing
def test_twiml_recording_configuration_and_order():
    """Verify TwiML XML has Start/Recording before Connect/Stream and has dual channels, track=both, trim=do-not-trim, and contains scenario_id."""
    from app.routers.voice import voice_incoming
    from fastapi import HTTPException
    
    # Mock settings
    settings = Settings(
        public_ws_base_url="wss://test.doobot.ai/voice/stream",
        public_http_base_url="https://test.doobot.ai",
        env_file=None
    )
    with patch("app.routers.voice.get_settings", return_value=settings):
        # Retrieve TwiML for seleccion_1
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(voice_incoming("seleccion_1"))
        
        xml_content = response.body.decode("utf-8")
        
        # 1. Assert Start/Recording before Connect/Stream
        start_idx = xml_content.find("<Start>")
        connect_idx = xml_content.find("<Connect>")
        assert start_idx != -1
        assert connect_idx != -1
        assert start_idx < connect_idx
        
        # 2. Verify configuration attributes
        assert 'channels="dual"' in xml_content
        assert 'track="both"' in xml_content
        assert 'trim="do-not-trim"' in xml_content
        
        # 3. Verify scenario_id and callback URL
        assert 'recordingStatusCallback="https://test.doobot.ai/voice/recording-status/seleccion_1"' in xml_content
        
        # Repeat for seleccion_2
        response_2 = loop.run_until_complete(voice_incoming("seleccion_2"))
        xml_content_2 = response_2.body.decode("utf-8")
        assert 'recordingStatusCallback="https://test.doobot.ai/voice/recording-status/seleccion_2"' in xml_content_2


def test_first_locution_contains_recording_warning():
    """Verify that initial_message of both scenarios starts with the recording warning."""
    from app.scenarios.registry import get_scenario
    
    sc_1 = get_scenario("seleccion_1")
    sc_2 = get_scenario("seleccion_2")
    
    warning = "Esta llamada está siendo grabada con fines de formación y evaluación."
    assert sc_1.initial_message.startswith(warning)
    assert sc_2.initial_message.startswith(warning)


@patch("app.routers.voice.GeminiVoiceSession")
def test_websocket_start_sets_requested_status(mock_session_class, monkeypatch):
    """Verify that when WebSocket start is received, recording is set to requested, and registry gets pending status."""
    def mock_get_settings():
        return Settings(gemini_api_key="dummy", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    mock_session_inst = MagicMock()
    mock_session_inst.connect = AsyncMock()
    mock_session_inst.close = AsyncMock()
    mock_session_class.return_value = mock_session_inst
    
    async def mock_receive():
        yield {"type": "setup_complete"}
        await asyncio.sleep(3600)
    mock_session_inst.receive = mock_receive
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_START_TEST")
    
    with client.websocket_connect("/voice/stream?scenario=seleccion_1") as websocket:
        websocket.send_json({"event": "connected"})
        websocket.send_json({
            "event": "start",
            "start": {
                "accountSid": "AC_TEST",
                "streamSid": "MZ_TEST",
                "callSid": "CA_START_TEST"
            }
        })
        import time
        time.sleep(0.2)
        
    assert mock_session_inst.recording_started is True
    assert mock_session_inst.recording_start_attempted is True
    assert mock_session_inst.recording_status == "requested"
    
    entry = registry.get("CA_START_TEST")
    assert entry is not None
    assert entry.consent_status == "pending"


@patch("twilio.request_validator.RequestValidator.validate")
def test_in_progress_callback_updates_registry_without_n8n(mock_validate, monkeypatch):
    """Verify that in-progress callback updates registry and returns 204 without sending event to n8n."""
    mock_validate.return_value = True
    
    def mock_get_settings():
        return Settings(twilio_auth_token="dummy", public_http_base_url="https://test.doobot.ai", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_IP_TEST")
    entry = registry.get_or_create("CA_IP_TEST", "seleccion_1")
    
    with patch("app.services.n8n_events.send_n8n_event") as mock_n8n:
        response = client.post(
            "/voice/recording-status/seleccion_1",
            data={
                "CallSid": "CA_IP_TEST",
                "RecordingSid": "RE_IP_123",
                "RecordingStatus": "in-progress"
            },
            headers={"X-Twilio-Signature": "dummy"}
        )
        assert response.status_code == 204
        mock_n8n.assert_not_called()
        
    assert entry.recording_sid == "RE_IP_123"
    assert entry.recording_status == "in-progress"


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_completed_callback_accepted_consent(mock_n8n, mock_validate, monkeypatch):
    """Verify that completed callback with accepted consent sends post_call_audio immediately."""
    mock_validate.return_value = True
    def mock_get_settings():
        return Settings(twilio_auth_token="dummy", public_http_base_url="https://test.doobot.ai", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_ACCEPTED_TEST")
    entry = registry.get_or_create("CA_ACCEPTED_TEST", "seleccion_1")
    entry.consent_status = "accepted"
    
    response = client.post(
        "/voice/recording-status/seleccion_1",
        data={
            "CallSid": "CA_ACCEPTED_TEST",
            "RecordingSid": "RE_ACC_123",
            "RecordingStatus": "completed",
            "RecordingUrl": "https://api.twilio.com/rec"
        },
        headers={"X-Twilio-Signature": "dummy"}
    )
    assert response.status_code == 204
    
    import time
    time.sleep(0.1)
    mock_n8n.assert_called_once()
    args, _ = mock_n8n.call_args
    assert args[0] == "recording.completed"
    assert args[1]["type"] == "post_call_audio"
    assert args[1]["data"]["recording"]["url"] == "https://api.twilio.com/rec"


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_completed_callback_rejected_consent(mock_n8n, mock_validate, monkeypatch):
    """Verify that completed callback with rejected consent does not send event to n8n."""
    mock_validate.return_value = True
    def mock_get_settings():
        return Settings(twilio_auth_token="dummy", public_http_base_url="https://test.doobot.ai", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_REJECTED_TEST")
    entry = registry.get_or_create("CA_REJECTED_TEST", "seleccion_1")
    entry.consent_status = "rejected"
    
    response = client.post(
        "/voice/recording-status/seleccion_1",
        data={
            "CallSid": "CA_REJECTED_TEST",
            "RecordingSid": "RE_REJ_123",
            "RecordingStatus": "completed",
            "RecordingUrl": "https://api.twilio.com/rec"
        },
        headers={"X-Twilio-Signature": "dummy"}
    )
    assert response.status_code == 204
    import time
    time.sleep(0.1)
    mock_n8n.assert_not_called()


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_completed_callback_pending_then_accepted_or_rejected(mock_n8n, mock_validate, monkeypatch):
    """Verify that completed callback with pending consent is stored, then sent on accepted or dropped on rejected."""
    mock_validate.return_value = True
    def mock_get_settings():
        return Settings(twilio_auth_token="dummy", public_http_base_url="https://test.doobot.ai", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_PENDING_TEST")
    entry = registry.get_or_create("CA_PENDING_TEST", "seleccion_1")
    entry.consent_status = "pending"
    
    response = client.post(
        "/voice/recording-status/seleccion_1",
        data={
            "CallSid": "CA_PENDING_TEST",
            "RecordingSid": "RE_PEND_123",
            "RecordingStatus": "completed",
            "RecordingUrl": "https://api.twilio.com/rec"
        },
        headers={"X-Twilio-Signature": "dummy"}
    )
    assert response.status_code == 204
    import time
    time.sleep(0.1)
    mock_n8n.assert_not_called()
    assert entry.pending_recording_event is not None
    
    # Simulate later acceptance
    session = GeminiVoiceSession(settings=mock_get_settings(), system_instruction="")
    session.provisional_name = "Daniel"
    session.provisional_lastname = "Martínez"
    session.call_sid = "CA_PENDING_TEST"
    session.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE
    
    session.commit_candidate_context()
    time.sleep(0.1)
    mock_n8n.assert_called_once()
    assert entry.pending_recording_event is None # check cleared
    
    # Test pending then rejected
    registry.remove("CA_PENDING_TEST2")
    entry2 = registry.get_or_create("CA_PENDING_TEST2", "seleccion_1")
    entry2.consent_status = "pending"
    
    client.post(
        "/voice/recording-status/seleccion_1",
        data={
            "CallSid": "CA_PENDING_TEST2",
            "RecordingSid": "RE_PEND_456",
            "RecordingStatus": "completed",
            "RecordingUrl": "https://api.twilio.com/rec"
        },
        headers={"X-Twilio-Signature": "dummy"}
    )
    assert entry2.pending_recording_event is not None
    
    # Simulate rejection
    session2 = GeminiVoiceSession(settings=mock_get_settings(), system_instruction="")
    session2.provisional_name = "Daniel"
    session2.provisional_lastname = "Martínez"
    session2.call_sid = "CA_PENDING_TEST2"
    session2.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE
    
    session2.process_user_transcript("No acepto.")
    assert entry2.pending_recording_event is None
    assert entry2.consent_status == "rejected"


@patch("twilio.request_validator.RequestValidator.validate")
@patch("app.services.n8n_events.send_n8n_event", new_callable=AsyncMock)
def test_completed_callback_unknown_call_sid(mock_n8n, mock_validate, monkeypatch):
    """Verify that completed callback for unknown call_sid does not send event to n8n."""
    mock_validate.return_value = True
    def mock_get_settings():
        return Settings(twilio_auth_token="dummy", public_http_base_url="https://test.doobot.ai", env_file=None)
    monkeypatch.setattr("app.routers.voice.get_settings", mock_get_settings)
    
    from app.services.call_consent_registry import registry
    registry.remove("CA_UNKNOWN_TEST")
    
    response = client.post(
        "/voice/recording-status/seleccion_1",
        data={
            "CallSid": "CA_UNKNOWN_TEST",
            "RecordingSid": "RE_UNK_123",
            "RecordingStatus": "completed",
            "RecordingUrl": "https://api.twilio.com/rec"
        },
        headers={"X-Twilio-Signature": "dummy"}
    )
    assert response.status_code == 204
    import time
    time.sleep(0.1)
    mock_n8n.assert_not_called()


# ── 12. Silence Reminder & Local VAD tests

def test_gemini_setup_contains_vad_configuration():
    """Verify Gemini setup message has startOfSpeechSensitivity, endOfSpeechSensitivity, and configured silenceDurationMs."""
    settings = Settings(
        gemini_vad_prefix_padding_ms=25,
        gemini_vad_silence_duration_ms=750,
        env_file=None
    )
    session = GeminiVoiceSession(settings=settings, system_instruction="system")
    setup_msg = session._build_setup_message()
    
    realtime_config = setup_msg["setup"]["realtimeInputConfig"]
    vad_config = realtime_config["automaticActivityDetection"]
    
    assert vad_config["startOfSpeechSensitivity"] == "START_SENSITIVITY_HIGH"
    assert vad_config["endOfSpeechSensitivity"] == "END_SENSITIVITY_LOW"
    assert vad_config["prefixPaddingMs"] == 25
    assert vad_config["silenceDurationMs"] == 750
    assert realtime_config["activityHandling"] == "START_OF_ACTIVITY_INTERRUPTS"
    assert realtime_config["turnCoverage"] == "TURN_INCLUDES_ONLY_ACTIVITY"


@pytest.mark.asyncio
async def test_silence_watch_timers_and_triggers():
    """Test timer trigger timing, start constraints, and cancellation scenarios."""
    settings = Settings(
        silence_reminder_enabled=True,
        silence_reminder_seconds=0.1,  # Short timeout for testing
        env_file=None
    )
    session = GeminiVoiceSession(settings=settings, system_instruction="system")
    session.stream_sid = "MZ123"
    session._ready = True
    session.call_sid = "CA123"
    
    # 1. No se inicia el contador hasta recibir el mark
    assert session.silence_watch_task is None
    
    # 2. El mark correcto inicia el temporizador
    session.twilio_playback_mark = "bot_turn_end:1"
    # Run handle_twilio_mark task
    await session.handle_twilio_mark("bot_turn_end:1")
    assert session.silence_watch_task is not None
    assert session.awaiting_user_response is True
    
    # 3. Un mark antiguo no inicia el temporizador
    session.silence_watch_task = None
    await session.handle_twilio_mark("bot_turn_end:0") # old mark
    assert session.silence_watch_task is None
    
    # 4. El recordatorio se dispara e incrementa el silence_reminder_index
    session.twilio_playback_mark = "bot_turn_end:2"
    await session.handle_twilio_mark("bot_turn_end:2")
    # Wait for the task to complete
    await asyncio.sleep(0.15)
    assert session.silence_reminder_sent is True
    assert session.silence_reminder_active is True
    assert session.silence_reminder_index == 1 # circular increment from 0 to 1
    
    # 5. Solo un recordatorio por espera (max per wait)
    # Even if we run watch again, it shouldn't trigger since silence_reminder_sent is True
    session.silence_watch_task = None
    session.awaiting_user_response = True
    await session.start_silence_watch()
    await asyncio.sleep(0.15)
    # Check index didn't increment again
    assert session.silence_reminder_index == 1


@pytest.mark.asyncio
async def test_silence_watch_cancellation_on_user_speech():
    """Verify that user speech cancels silence timer and clears Twilio playback buffers."""
    settings = Settings(
        silence_reminder_enabled=True,
        silence_reminder_seconds=0.2,
        env_file=None
    )
    session = GeminiVoiceSession(settings=settings, system_instruction="system")
    session.stream_sid = "MZ123"
    session._ready = True
    session.call_sid = "CA123"
    
    # Pre-seed clear callback mock
    mock_clear = AsyncMock()
    session.on_twilio_clear = mock_clear
    
    # Start watch
    session.twilio_playback_mark = "bot_turn_end:1"
    await session.handle_twilio_mark("bot_turn_end:1")
    assert session.silence_watch_task is not None
    
    # User speaks at 0.05s (before 0.2s timeout)
    await asyncio.sleep(0.05)
    session.on_user_speech_start()
    
    # Wait to ensure timer would have fired
    await asyncio.sleep(0.2)
    # Should not have triggered reminder
    assert session.silence_reminder_sent is False
    assert session.user_speaking is True
    
    # Verify that if silence reminder was active, user speaking clears twilio buffer
    session.silence_reminder_active = True
    session.on_user_speech_start()
    await asyncio.sleep(0.05)
    mock_clear.assert_called_once()
    assert session.silence_reminder_active is False


def test_local_vad_rms_noise_filtering():
    """Verify that a single noisy frame does not activate VAD, but multiple frames do."""
    settings = Settings(
        user_speech_rms_threshold=400,
        user_speech_min_active_ms=50,
        user_speech_hangover_ms=100,
        env_file=None
    )
    session = GeminiVoiceSession(settings=settings, system_instruction="system")
    
    # Create empty, low-rms audio (160 bytes of zero)
    silence = b"\x00" * 160
    # High RMS noise frame (rms = 500)
    import audioop
    noise_frame = b"\x00\x7f" * 80
    
    # 1. Single noisy frame shouldn't trigger VAD since min_active_ms is 50
    session.process_input_audio_for_vad(noise_frame)
    assert session.user_speaking is False
    
    # 2. Multiple active frames exceeding 50ms should trigger VAD
    import time
    # Simulate consecutive active frames spanning 60ms
    session.process_input_audio_for_vad(noise_frame)
    session._vad_speech_start_time = time.monotonic() - 0.06 # backdate start to simulate duration
    session.process_input_audio_for_vad(noise_frame)
    
    assert session.user_speaking is True


@pytest.mark.asyncio
async def test_silence_watch_ignored_in_finished_or_reconnecting_states():
    """Verify that silence reminder is not started during bot playback, finished states, RGPD rejection, or reconnection."""
    settings = Settings(
        silence_reminder_enabled=True,
        silence_reminder_seconds=0.1,
        env_file=None
    )
    session = GeminiVoiceSession(settings=settings, system_instruction="system")
    session.stream_sid = "MZ123"
    session._ready = True
    session.call_sid = "CA123"
    session.twilio_playback_mark = "bot_turn_end:1"
    
    # 1. Ignored if phase is ROLEPLAY_FINISHED
    session.onboarding_phase = OnboardingPhase.ROLEPLAY_FINISHED
    await session.handle_twilio_mark("bot_turn_end:1")
    assert session.silence_watch_task is None
    
    # 2. Ignored if RGPD rejected
    session.onboarding_phase = OnboardingPhase.WAITING_RGPD_ACCEPTANCE
    session.candidate_context["rgpd_ok"] = False
    await session.handle_twilio_mark("bot_turn_end:1")
    assert session.silence_watch_task is None
    
    # 3. Ignored if reconnecting
    session.candidate_context["rgpd_ok"] = True
    session._reconnecting = True
    await session.handle_twilio_mark("bot_turn_end:1")
    assert session.silence_watch_task is None





