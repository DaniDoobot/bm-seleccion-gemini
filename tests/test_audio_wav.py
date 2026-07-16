"""Unit tests for WAV utilities and µ-law decoder added to audio.py.

These tests validate:
  - pcm_bytes_to_wav: valid output, WAV header, sample rate, channel count.
  - mulaw_bytes_to_pcm: decodes µ-law to PCM correctly.
  - Both functions raise ValueError on empty input.
  - Round-trip: PCM → µ-law → PCM preserves approximate amplitude.

No real Gemini or Twilio connection is made.
"""
import base64
import io
import math
import struct
import wave

import pytest

from app.core.audio import (
    AUDIO_CHANNELS,
    GEMINI_OUTPUT_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH,
    TWILIO_SAMPLE_RATE,
    encode_gemini_to_twilio,
    mulaw_bytes_to_pcm,
    pcm_bytes_to_wav,
)

try:
    import audioop
except ImportError:
    import audioop_lts as audioop


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sine_pcm(num_samples: int, sample_rate: int, frequency: float = 440.0) -> bytes:
    """Generate a mono 16-bit sine wave at the given frequency."""
    amplitude = 10_000
    samples = [
        int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        for i in range(num_samples)
    ]
    samples = [max(-32768, min(32767, s)) for s in samples]
    return struct.pack(f"<{num_samples}h", *samples)


def _read_wav_params(wav_bytes: bytes) -> wave.Wave_read:
    """Parse WAV bytes and return a wave.Wave_read for header inspection."""
    return wave.open(io.BytesIO(wav_bytes))


# ── pcm_bytes_to_wav ──────────────────────────────────────────────────────────

class TestPcmBytesToWav:

    def test_returns_bytes(self):
        pcm = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        result = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        assert isinstance(result, bytes)

    def test_starts_with_riff_header(self):
        pcm = _sine_pcm(240, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        # RIFF WAV files start with b"RIFF"
        assert wav[:4] == b"RIFF"

    def test_contains_wave_marker(self):
        pcm = _sine_pcm(240, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        assert b"WAVE" in wav[:12]

    def test_sample_rate_24k(self):
        pcm = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        with _read_wav_params(wav) as wf:
            assert wf.getframerate() == GEMINI_OUTPUT_SAMPLE_RATE

    def test_sample_rate_8k(self):
        pcm = _sine_pcm(160, TWILIO_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, TWILIO_SAMPLE_RATE)
        with _read_wav_params(wav) as wf:
            assert wf.getframerate() == TWILIO_SAMPLE_RATE

    def test_sample_rate_16k(self):
        pcm = _sine_pcm(320, 16_000)
        wav = pcm_bytes_to_wav(pcm, 16_000)
        with _read_wav_params(wav) as wf:
            assert wf.getframerate() == 16_000

    def test_mono_channel(self):
        pcm = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        with _read_wav_params(wav) as wf:
            assert wf.getnchannels() == 1

    def test_16bit_sample_width(self):
        pcm = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        with _read_wav_params(wav) as wf:
            assert wf.getsampwidth() == 2  # 16-bit = 2 bytes

    def test_frame_count_matches_input(self):
        num_samples = 720
        pcm = _sine_pcm(num_samples, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        with _read_wav_params(wav) as wf:
            assert wf.getnframes() == num_samples

    def test_wav_larger_than_pcm_due_to_header(self):
        """WAV output must be larger than the raw PCM input (has a header)."""
        pcm = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm, GEMINI_OUTPUT_SAMPLE_RATE)
        assert len(wav) > len(pcm)

    def test_empty_input_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            pcm_bytes_to_wav(b"", GEMINI_OUTPUT_SAMPLE_RATE)

    def test_custom_sample_rate(self):
        pcm = _sine_pcm(100, 44_100)
        wav = pcm_bytes_to_wav(pcm, sample_rate=44_100)
        with _read_wav_params(wav) as wf:
            assert wf.getframerate() == 44_100


# ── mulaw_bytes_to_pcm ────────────────────────────────────────────────────────

class TestMulawBytesToPcm:

    def _make_mulaw(self, num_samples: int) -> bytes:
        """Generate µ-law bytes from a sine wave."""
        pcm = _sine_pcm(num_samples, TWILIO_SAMPLE_RATE)
        return audioop.lin2ulaw(pcm, PCM_SAMPLE_WIDTH)

    def test_returns_bytes(self):
        mulaw = self._make_mulaw(160)
        result = mulaw_bytes_to_pcm(mulaw)
        assert isinstance(result, bytes)

    def test_output_is_double_length_of_input(self):
        """µ-law is 1 byte/sample; PCM 16-bit is 2 bytes/sample."""
        num_samples = 160
        mulaw = self._make_mulaw(num_samples)
        pcm = mulaw_bytes_to_pcm(mulaw)
        assert len(pcm) == num_samples * 2

    def test_output_is_valid_16bit_pcm(self):
        """Output length must be a multiple of 2."""
        mulaw = self._make_mulaw(160)
        pcm = mulaw_bytes_to_pcm(mulaw)
        assert len(pcm) % 2 == 0

    def test_roundtrip_preserves_approximate_amplitude(self):
        """PCM → µ-law → PCM should not dramatically change amplitude.

        G.711 µ-law compression is lossy but preserves loudness within
        a reasonable margin. We check that RMS energy is in the same
        order of magnitude.
        """
        num_samples = 320
        original_pcm = _sine_pcm(num_samples, TWILIO_SAMPLE_RATE, frequency=440.0)
        mulaw = audioop.lin2ulaw(original_pcm, PCM_SAMPLE_WIDTH)
        decoded_pcm = mulaw_bytes_to_pcm(mulaw)

        def rms(pcm_bytes: bytes) -> float:
            count = len(pcm_bytes) // 2
            shorts = struct.unpack(f"<{count}h", pcm_bytes)
            return math.sqrt(sum(float(s) ** 2 for s in shorts) / count)

        rms_orig = rms(original_pcm)
        rms_decoded = rms(decoded_pcm)
        # Allow up to 50% deviation (µ-law is logarithmic, not lossless)
        assert rms_decoded > rms_orig * 0.5, (
            f"RMS dropped too much: original={rms_orig:.1f}, decoded={rms_decoded:.1f}"
        )

    def test_empty_input_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            mulaw_bytes_to_pcm(b"")


# ── Integration: encode_gemini_to_twilio → mulaw_bytes_to_pcm → pcm_bytes_to_wav

class TestEndToEndWavPipeline:
    """Verify that the full Gemini→Twilio WAV pipeline works without errors."""

    def test_gemini_audio_to_twilio_wav(self):
        """Simulate collecting a Gemini audio chunk and writing a Twilio WAV."""
        import base64

        # Simulate a Gemini PCM 24 kHz chunk
        pcm_24k = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        b64_chunk = base64.b64encode(pcm_24k).decode()

        # Step 1: Convert to µ-law 8 kHz (streaming converter)
        mulaw_b64, _ = encode_gemini_to_twilio(b64_chunk, None)
        assert mulaw_b64 is not None

        # Step 2: Decode µ-law bytes
        mulaw_bytes = base64.b64decode(mulaw_b64)
        pcm_8k = mulaw_bytes_to_pcm(mulaw_bytes)
        assert pcm_8k

        # Step 3: Wrap in WAV
        wav = pcm_bytes_to_wav(pcm_8k, sample_rate=TWILIO_SAMPLE_RATE)
        assert wav[:4] == b"RIFF"

        # Verify WAV header
        with wave.open(io.BytesIO(wav)) as wf:
            assert wf.getframerate() == TWILIO_SAMPLE_RATE
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2

    def test_gemini_pcm_to_24k_wav(self):
        """Simulate writing Gemini raw output directly to a 24 kHz WAV."""
        pcm_24k = _sine_pcm(480, GEMINI_OUTPUT_SAMPLE_RATE)
        wav = pcm_bytes_to_wav(pcm_24k, sample_rate=GEMINI_OUTPUT_SAMPLE_RATE)
        assert wav[:4] == b"RIFF"
        with wave.open(io.BytesIO(wav)) as wf:
            assert wf.getframerate() == GEMINI_OUTPUT_SAMPLE_RATE
