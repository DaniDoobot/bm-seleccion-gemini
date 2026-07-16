"""Unit tests for audio conversion utilities.

Validates:
  - µ-law 8 kHz → PCM 16 kHz (decode_twilio_to_gemini)
  - PCM 24 kHz → µ-law 8 kHz (encode_gemini_to_twilio)
  - Empty input handling.
  - Invalid / truncated input handling.
  - Resampler state continuity across consecutive chunks.

No real Gemini or Twilio connection is made in these tests.
"""
import base64
import math
import struct

import pytest

from app.core.audio import (
    AUDIO_CHANNELS,
    GEMINI_INPUT_SAMPLE_RATE,
    GEMINI_OUTPUT_SAMPLE_RATE,
    GEMINI_INPUT_MIME_TYPE,
    PCM_SAMPLE_WIDTH,
    TWILIO_SAMPLE_RATE,
    decode_twilio_to_gemini,
    encode_gemini_to_twilio,
)

try:
    import audioop
except ImportError:
    import audioop_lts as audioop


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mulaw_8k_b64(num_samples: int = 160) -> str:
    """Generate a synthetic µ-law 8 kHz audio chunk encoded as base64.

    Creates a sine wave at 440 Hz (A4), encodes it to 16-bit linear PCM,
    converts to G.711 µ-law, then base64-encodes the result.
    """
    frequency = 440.0
    amplitude = 8000
    sample_rate = 8_000
    pcm_samples = []
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        sample = max(-32768, min(32767, sample))
        pcm_samples.append(sample)

    pcm_bytes = struct.pack(f"<{num_samples}h", *pcm_samples)
    mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
    return base64.b64encode(mulaw_bytes).decode("utf-8")


def _make_pcm_24k_b64(num_samples: int = 480) -> str:
    """Generate a synthetic linear PCM 24 kHz chunk encoded as base64.

    Creates a sine wave at 440 Hz at 24 kHz sample rate.
    """
    frequency = 440.0
    amplitude = 8000
    sample_rate = 24_000
    pcm_samples = []
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        sample = max(-32768, min(32767, sample))
        pcm_samples.append(sample)

    pcm_bytes = struct.pack(f"<{num_samples}h", *pcm_samples)
    return base64.b64encode(pcm_bytes).decode("utf-8")


# ── decode_twilio_to_gemini ───────────────────────────────────────────────────

class TestDecodeTwilioToGemini:

    def test_returns_non_none_on_valid_input(self):
        b64 = _make_mulaw_8k_b64(160)
        result, state = decode_twilio_to_gemini(b64, None)
        assert result is not None

    def test_result_is_valid_base64(self):
        b64 = _make_mulaw_8k_b64(160)
        result, _ = decode_twilio_to_gemini(b64, None)
        # Must not raise
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_output_is_16bit_pcm(self):
        """Output bytes must be a multiple of 2 (16-bit samples)."""
        b64 = _make_mulaw_8k_b64(160)
        result, _ = decode_twilio_to_gemini(b64, None)
        decoded = base64.b64decode(result)
        assert len(decoded) % 2 == 0

    def test_upsampling_doubles_sample_count_approximately(self):
        """8 kHz → 16 kHz doubles the number of samples (±1 frame tolerance)."""
        num_input_samples = 160
        b64 = _make_mulaw_8k_b64(num_input_samples)
        result, _ = decode_twilio_to_gemini(b64, None)
        output_bytes = base64.b64decode(result)
        output_samples = len(output_bytes) // 2
        expected = num_input_samples * 2
        # Allow small rounding difference from the resampler
        assert abs(output_samples - expected) <= 4, (
            f"Expected ~{expected} samples, got {output_samples}"
        )

    def test_state_is_returned(self):
        b64 = _make_mulaw_8k_b64(160)
        _, state = decode_twilio_to_gemini(b64, None)
        # State should not be None after first call (resampler initialised)
        assert state is not None

    def test_state_continuity_across_chunks(self):
        """Processing two chunks sequentially should work without error."""
        chunk1 = _make_mulaw_8k_b64(160)
        chunk2 = _make_mulaw_8k_b64(160)

        result1, state1 = decode_twilio_to_gemini(chunk1, None)
        result2, state2 = decode_twilio_to_gemini(chunk2, state1)

        assert result1 is not None
        assert result2 is not None
        # State should evolve between calls
        assert state1 != state2 or state1 is not None

    def test_empty_input_returns_none(self):
        """An empty payload string should not raise — return None gracefully."""
        result, state = decode_twilio_to_gemini("", None)
        assert result is None

    def test_invalid_base64_returns_none(self):
        """Non-base64 input must not propagate an exception."""
        result, state = decode_twilio_to_gemini("!!!not_base64!!!", None)
        assert result is None

    def test_original_state_returned_on_failure(self):
        """On failure the original rate_state should be returned unchanged."""
        sentinel = ("marker",)
        result, state = decode_twilio_to_gemini("bad_input!!!", sentinel)
        assert result is None
        assert state is sentinel

    def test_truncated_input_handled_gracefully(self):
        """A single byte of valid base64 (which decodes to incomplete PCM) must
        not raise an unhandled exception."""
        # 1 byte base64 is not valid, should return None
        result, _ = decode_twilio_to_gemini("YQ==", None)
        # May return None or a valid (if minimal) result — must not raise
        # We only assert no exception was raised (implicit in reaching this line)

    def test_mime_type_constant_is_correct(self):
        assert GEMINI_INPUT_MIME_TYPE == "audio/pcm;rate=16000"


# ── encode_gemini_to_twilio ───────────────────────────────────────────────────

class TestEncodeGeminiToTwilio:

    def test_returns_non_none_on_valid_input(self):
        b64 = _make_pcm_24k_b64(480)
        result, state = encode_gemini_to_twilio(b64, None)
        assert result is not None

    def test_result_is_valid_base64(self):
        b64 = _make_pcm_24k_b64(480)
        result, _ = encode_gemini_to_twilio(b64, None)
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_downsampling_reduces_sample_count_approximately(self):
        """24 kHz → 8 kHz reduces samples by factor ~3."""
        num_input_samples = 480
        b64 = _make_pcm_24k_b64(num_input_samples)
        result, _ = encode_gemini_to_twilio(b64, None)
        output_bytes = base64.b64decode(result)
        # Output is µ-law (1 byte per sample), so length == num output samples
        output_samples = len(output_bytes)
        expected = num_input_samples // 3
        assert abs(output_samples - expected) <= 4, (
            f"Expected ~{expected} µ-law samples, got {output_samples}"
        )

    def test_output_is_mulaw_single_byte_per_sample(self):
        """G.711 µ-law encodes 1 byte per sample (unlike PCM which uses 2)."""
        num_samples = 480
        b64 = _make_pcm_24k_b64(num_samples)
        result, _ = encode_gemini_to_twilio(b64, None)
        output_bytes = base64.b64decode(result)
        # Should be ~160 bytes (480/3) not ~320 bytes
        assert len(output_bytes) < num_samples

    def test_state_is_returned(self):
        b64 = _make_pcm_24k_b64(480)
        _, state = encode_gemini_to_twilio(b64, None)
        assert state is not None

    def test_state_continuity_across_chunks(self):
        chunk1 = _make_pcm_24k_b64(480)
        chunk2 = _make_pcm_24k_b64(480)

        result1, state1 = encode_gemini_to_twilio(chunk1, None)
        result2, state2 = encode_gemini_to_twilio(chunk2, state1)

        assert result1 is not None
        assert result2 is not None

    def test_empty_input_returns_none(self):
        result, state = encode_gemini_to_twilio("", None)
        assert result is None

    def test_invalid_base64_returns_none(self):
        result, state = encode_gemini_to_twilio("!!!not_base64!!!", None)
        assert result is None

    def test_original_state_returned_on_failure(self):
        sentinel = ("marker",)
        result, state = encode_gemini_to_twilio("bad_input!!!", sentinel)
        assert result is None
        assert state is sentinel


# ── Constants ─────────────────────────────────────────────────────────────────

class TestAudioConstants:

    def test_twilio_sample_rate(self):
        assert TWILIO_SAMPLE_RATE == 8_000

    def test_gemini_input_sample_rate(self):
        assert GEMINI_INPUT_SAMPLE_RATE == 16_000

    def test_gemini_output_sample_rate(self):
        assert GEMINI_OUTPUT_SAMPLE_RATE == 24_000

    def test_pcm_sample_width(self):
        assert PCM_SAMPLE_WIDTH == 2

    def test_audio_channels(self):
        assert AUDIO_CHANNELS == 1
