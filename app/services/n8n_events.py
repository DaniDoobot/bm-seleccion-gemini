import asyncio
import logging
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_n8n_event(
    event_type: str,
    payload: dict,
    idempotency_key: str,
) -> bool:
    """Send a generic simulation event to n8n webhook with a retry policy."""
    settings = get_settings()
    if not settings.n8n_events_webhook_url:
        logger.warning("N8N_EVENTS_WEBHOOK_URL not configured. Skipping event dispatch.")
        return False

    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }
    if settings.n8n_webhook_token:
        headers["Authorization"] = f"Bearer {settings.n8n_webhook_token}"

    url = settings.n8n_events_webhook_url
    max_attempts = 3
    timeout = 10.0

    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                
            # Success range: 2xx
            if 200 <= response.status_code < 300:
                logger.info("Successfully sent event %s to n8n (attempt %d).", event_type, attempt + 1)
                return True
                
            # Do NOT retry 4xx errors
            if 400 <= response.status_code < 500:
                logger.error(
                    "n8n returned 4xx status code %d for event %s. Not retrying.",
                    response.status_code,
                    event_type
                )
                return False
                
            # 5xx error
            logger.warning(
                "n8n returned 5xx status code %d for event %s (attempt %d).",
                response.status_code,
                event_type,
                attempt + 1
            )
            
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            logger.warning(
                "Network/timeout error while sending event %s to n8n (attempt %d): %s",
                event_type,
                attempt + 1,
                type(exc).__name__
            )
        except Exception as exc:
            logger.warning(
                "Unexpected error while sending event %s to n8n (attempt %d): %s",
                event_type,
                attempt + 1,
                type(exc).__name__
            )

        # Apply backoff (only if not on the last attempt)
        if attempt < max_attempts - 1:
            backoff_seconds = 1.0 * (2 ** attempt)
            await asyncio.sleep(backoff_seconds)

    logger.error("Failed to send event %s to n8n after %d attempts.", event_type, max_attempts)
    return False
