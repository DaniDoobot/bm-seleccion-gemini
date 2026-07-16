"""Core audio conversion utilities for the Gemini ↔ Twilio audio bridge.

Twilio delivers audio as G.711 µ-law encoded at 8 kHz.
Gemini Live expects and produces 16-bit linear PCM at 16 kHz (input)
and 24 kHz (output).

Conversion pipeline:
  Twilio → Gemini:  µ-law 8kHz  → lin16 8kHz  → lin16 16kHz  → base64
  Gemini → Twilio:  base64      → lin16 24kHz  → lin16 8kHz   → µ-law 8kHz → base64

These functions are intentionally stateless regarding Twilio/Gemini sessions;
the caller manages the resampler state (rate_state) between consecutive chunks
so that the resampling filter can maintain continuity across frames.

The implementation mirrors the approach validated in the bm-analysis-service
reference project. Parameters (sample rates, codec) are not changed in this
phase.

WAV utilities
-------------
pcm_bytes_to_wav   — Wrap raw linear PCM bytes in a RIFF WAV container.
mulaw_bytes_to_pcm — Decode G.711 µ-law bytes to 16-bit linear PCM.

These helpers are used by the integration test script to persist audio
captures to disk for manual listening. They are not part of the real-time
Twilio↔Gemini path.
"""
import base64
import io
import logging
import wave
from typing import Optional

logger = logging.getLogger(__name__)

# Twilio delivers µ-law G.711 at this sample rate.
TWILIO_SAMPLE_RATE = 8_000

# Gemini Live expects linear PCM at this rate on input.
GEMINI_INPUT_SAMPLE_RATE = 16_000

# Gemini Live produces linear PCM at this rate on output.
GEMINI_OUTPUT_SAMPLE_RATE = 24_000

# PCM sample width in bytes (16-bit = 2 bytes per sample).
PCM_SAMPLE_WIDTH = 2

# Number of audio channels (mono telephone audio).
AUDIO_CHANNELS = 1

# MIME type sent to Gemini for input audio chunks.
GEMINI_INPUT_MIME_TYPE = f"audio/pcm;rate={GEMINI_INPUT_SAMPLE_RATE}"

try:
    import audioop  # Available on Python < 3.13
except ImportError:
    import audioop_lts as audioop  # Backport for Python >= 3.13


# ── Real-time streaming converters ────────────────────────────────────────────

def decode_twilio_to_gemini(
    base64_payload: str,
    rate_state: Optional[tuple],
) -> tuple[Optional[str], Optional[tuple]]:
    """Convert a Twilio media chunk to a Gemini-compatible audio chunk.

    Args:
        base64_payload: Base64-encoded G.711 µ-law audio at 8 kHz from Twilio.
        rate_state: Resampler state from the previous call (None on first call).
                    Must be threaded through successive calls to preserve
                    continuity of the resampling filter.

    Returns:
        A tuple of:
          - Base64-encoded 16-bit linear PCM at 16 kHz ready for Gemini,
            or None if conversion failed.
          - Updated resampler state to pass to the next call.
    """
    if not base64_payload:
        return None, rate_state
    try:
        # 1. Decode base64 envelope
        mulaw_bytes = base64.b64decode(base64_payload)

        # 2. G.711 µ-law → 16-bit linear PCM (still at 8 kHz)
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, PCM_SAMPLE_WIDTH)

        # 3. Resample 8 kHz → 16 kHz (mono, 2 bytes/sample)
        pcm_16k, new_state = audioop.ratecv(
            pcm_8k,
            PCM_SAMPLE_WIDTH,
            AUDIO_CHANNELS,
            TWILIO_SAMPLE_RATE,
            GEMINI_INPUT_SAMPLE_RATE,
            rate_state,
        )

        # 4. Re-encode as base64
        payload = base64.b64encode(pcm_16k).decode("utf-8")
        return payload, new_state

    except Exception as exc:
        logger.error("decode_twilio_to_gemini failed: %s", exc)
        return None, rate_state


def encode_gemini_to_twilio(
    base64_payload: str,
    rate_state: Optional[tuple],
) -> tuple[Optional[str], Optional[tuple]]:
    """Convert a Gemini audio output chunk to a Twilio-compatible media chunk.

    Args:
        base64_payload: Base64-encoded 16-bit linear PCM at 24 kHz from Gemini.
        rate_state: Resampler state from the previous call (None on first call).

    Returns:
        A tuple of:
          - Base64-encoded G.711 µ-law audio at 8 kHz ready for Twilio,
            or None if conversion failed.
          - Updated resampler state to pass to the next call.
    """
    if not base64_payload:
        return None, rate_state
    try:
        # 1. Decode base64 envelope
        pcm_24k = base64.b64decode(base64_payload)

        # 2. Resample 24 kHz → 8 kHz (mono, 2 bytes/sample)
        pcm_8k, new_state = audioop.ratecv(
            pcm_24k,
            PCM_SAMPLE_WIDTH,
            AUDIO_CHANNELS,
            GEMINI_OUTPUT_SAMPLE_RATE,
            TWILIO_SAMPLE_RATE,
            rate_state,
        )

        # 3. 16-bit linear PCM → G.711 µ-law
        mulaw_bytes = audioop.lin2ulaw(pcm_8k, PCM_SAMPLE_WIDTH)

        # 4. Re-encode as base64
        payload = base64.b64encode(mulaw_bytes).decode("utf-8")
        return payload, new_state

    except Exception as exc:
        logger.error("encode_gemini_to_twilio failed: %s", exc)
        return None, rate_state


# ── WAV file utilities (used by test scripts and offline tools) ───────────────

def pcm_bytes_to_wav(
    pcm_data: bytes,
    sample_rate: int,
    channels: int = AUDIO_CHANNELS,
    sample_width: int = PCM_SAMPLE_WIDTH,
) -> bytes:
    """Wrap raw 16-bit linear PCM bytes in a RIFF WAV container.

    Args:
        pcm_data:     Raw little-endian 16-bit PCM samples.
        sample_rate:  Sample rate in Hz (e.g. 24000, 16000, 8000).
        channels:     Number of audio channels (default: 1 — mono).
        sample_width: Bytes per sample (default: 2 — 16-bit).

    Returns:
        A complete WAV file as bytes (with RIFF header), suitable for
        writing directly to a .wav file or playing back.

    Raises:
        ValueError: If pcm_data is empty.
    """
    if not pcm_data:
        raise ValueError("pcm_data must not be empty.")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def mulaw_bytes_to_pcm(mulaw_data: bytes) -> bytes:
    """Decode G.711 µ-law bytes to 16-bit linear PCM at 8 kHz.

    This is the inverse of ``audioop.lin2ulaw``. It is used when the
    Twilio-encoded audio needs to be written to a WAV file for auditing
    purposes (µ-law itself is not directly playable as a standard WAV).

    Args:
        mulaw_data: Raw G.711 µ-law encoded audio bytes.

    Returns:
        16-bit little-endian linear PCM bytes at 8 kHz (same number of
        samples, 2 bytes per sample).

    Raises:
        ValueError: If mulaw_data is empty.
    """
    if not mulaw_data:
        raise ValueError("mulaw_data must not be empty.")
    return audioop.ulaw2lin(mulaw_data, PCM_SAMPLE_WIDTH)
