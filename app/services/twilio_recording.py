import logging
from typing import Optional
from twilio.rest import Client

from app.config import get_settings

logger = logging.getLogger(__name__)


def start_twilio_recording(call_sid: str, scenario_id: str) -> Optional[str]:
    """Start Twilio recording for the active call using Twilio Python SDK client."""
    settings = get_settings()
    
    if not settings.call_recording_enabled:
        logger.info("Call recording is disabled via config.")
        return None

    account_sid = settings.twilio_account_sid
    auth_token = settings.twilio_auth_token
    
    if not account_sid or not auth_token:
        logger.error("Twilio credentials not configured. Cannot record call.")
        return None

    if not settings.public_http_base_url:
        logger.error("PUBLIC_HTTP_BASE_URL is not configured. Cannot set recording status callback.")
        return None

    # Construct status callback URL
    public_url_clean = settings.public_http_base_url.rstrip("/")
    callback_url = f"{public_url_clean}/voice/recording-status/{scenario_id}"
    
    try:
        # Initialize official Twilio REST client
        client = Client(account_sid, auth_token)
        
        # Start recording programmatically
        recording = client.calls(call_sid).recordings.create(
            recording_channels="dual",
            recording_track="both",
            recording_status_callback=callback_url,
            recording_status_callback_method="POST",
            recording_status_callback_event=["completed", "absent"],
            trim="do-not-trim"
        )
        
        logger.info(
            "Successfully started Twilio recording for call %s, recording_sid: %s, callback: %s",
            call_sid,
            recording.sid,
            callback_url
        )
        return recording.sid
        
    except Exception as exc:
        logger.error("Failed to start Twilio recording for call %s: %s", call_sid, exc)
        return None
