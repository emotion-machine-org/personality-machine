"""Background noise mixer for DialogMachine simulate sessions.

Adds subtle procedural ambience to assistant audio using Pipecat's output
transport mixer hook (`audio_out_mixer`).
"""

from __future__ import annotations

import logging
import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
from pipecat.frames.frames import MixerControlFrame, MixerEnableFrame, MixerUpdateSettingsFrame

logger = logging.getLogger(__name__)

BACKGROUND_NOISE_RESTAURANT = "restaurant_chatter"
BACKGROUND_NOISE_CITY = "city"
BACKGROUND_NOISE_OFFICE = "office_hum"
BACKGROUND_NOISE_CAFE_LEGACY = "cafe_ambience"

AVAILABLE_BACKGROUND_NOISE_TYPES = (
    BACKGROUND_NOISE_RESTAURANT,
    BACKGROUND_NOISE_CITY,
    BACKGROUND_NOISE_OFFICE,
)
DEFAULT_BACKGROUND_NOISE_TYPE = BACKGROUND_NOISE_RESTAURANT
_LEGACY_NOISE_TYPE_ALIASES = {
    BACKGROUND_NOISE_CAFE_LEGACY: BACKGROUND_NOISE_CITY,
}

# Keep ambience subtle so speech remains intelligible.
DEFAULT_BACKGROUND_NOISE_VOLUME = 0.12
_RMS_EPSILON = 1e-8
_NORMALIZE_EPSILON = 1e-9
_PCM_WIDTH_U8 = 1
_PCM_WIDTH_I16 = 2
_PCM_WIDTH_I24 = 3
_PCM_WIDTH_I32 = 4
_NOISE_SAMPLES_DIR = Path(__file__).with_name("noise_samples")
_NOISE_SAMPLE_FILES = {
    BACKGROUND_NOISE_RESTAURANT: _NOISE_SAMPLES_DIR / "restaurant_chatter.wav",
    BACKGROUND_NOISE_CITY: _NOISE_SAMPLES_DIR / "city.wav",
}


@dataclass(frozen=True)
class NoiseBurstSpec:
    low_hz: float
    high_hz: float
    events: int
    burst_seconds: float
    decay: float
    jitter: float


@dataclass(frozen=True)
class HarmonicBurstSpec:
    events: int
    burst_seconds: float
    base_hz: float
    overtone_hz: float
    decay: float
    jitter: float


def normalize_background_noise_type(noise_type: str | None) -> str:
    if noise_type in _LEGACY_NOISE_TYPE_ALIASES:
        return _LEGACY_NOISE_TYPE_ALIASES[noise_type]
    if noise_type in AVAILABLE_BACKGROUND_NOISE_TYPES:
        return noise_type
    return DEFAULT_BACKGROUND_NOISE_TYPE


def is_valid_background_noise_type(noise_type: str | None) -> bool:
    return (
        noise_type in AVAILABLE_BACKGROUND_NOISE_TYPES or noise_type in _LEGACY_NOISE_TYPE_ALIASES
    )


def clamp_background_noise_volume(value: Any) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKGROUND_NOISE_VOLUME
    return min(1.0, max(0.0, volume))


class ProceduralBackgroundNoiseMixer(BaseAudioMixer):
    """Mixer that overlays generated ambience loops behind assistant speech."""

    _LOOP_SECONDS = 16

    def __init__(
        self,
        *,
        default_sound: str = DEFAULT_BACKGROUND_NOISE_TYPE,
        volume: float = DEFAULT_BACKGROUND_NOISE_VOLUME,
        mixing: bool = True,
        loop: bool = True,
    ):
        self._volume = clamp_background_noise_volume(volume)
        self._sample_rate = 0
        self._mixing = mixing
        self._loop = loop
        self._sound_pos = 0
        self._current_sound = normalize_background_noise_type(default_sound)
        self._sounds: dict[str, np.ndarray] = {}

    async def start(self, sample_rate: int):
        self._sample_rate = sample_rate
        self._sounds = self._generate_loops(sample_rate)
        self._sound_pos = 0
        logger.info(
            "[VOICE_NOISE] Mixer started: sound=%s sample_rate=%s volume=%.2f",
            self._current_sound,
            sample_rate,
            self._volume,
        )

    async def stop(self):
        self._sounds = {}

    async def process_frame(self, frame: MixerControlFrame):
        if isinstance(frame, MixerUpdateSettingsFrame):
            await self._update_settings(frame.settings)
        elif isinstance(frame, MixerEnableFrame):
            self._mixing = bool(frame.enable)

    async def mix(self, audio: bytes) -> bytes:
        if not self._mixing or not audio:
            return audio

        sound = self._sounds.get(self._current_sound)
        if sound is None or sound.size == 0:
            return audio

        audio_np = np.frombuffer(audio, dtype=np.int16)
        if audio_np.size == 0:
            return audio

        noise_chunk = self._next_noise_chunk(sound, audio_np.size)
        mixed = np.clip(
            audio_np.astype(np.float32) + noise_chunk.astype(np.float32) * self._volume,
            -32768,
            32767,
        ).astype(np.int16)
        return mixed.tobytes()

    async def _update_settings(self, settings: dict[str, Any]):
        for setting, value in settings.items():
            if setting == "sound":
                self._current_sound = normalize_background_noise_type(str(value))
                self._sound_pos = 0
            elif setting == "volume":
                self._volume = clamp_background_noise_volume(value)
            elif setting == "loop":
                self._loop = bool(value)

    def _next_noise_chunk(self, sound: np.ndarray, chunk_size: int) -> np.ndarray:
        if chunk_size <= 0:
            return np.zeros(0, dtype=np.int16)

        start = self._sound_pos
        end = start + chunk_size

        if end <= sound.size:
            self._sound_pos = end
            return sound[start:end]

        # End-of-loop handling.
        if not self._loop:
            remaining = max(sound.size - start, 0)
            chunk = np.zeros(chunk_size, dtype=np.int16)
            if remaining > 0:
                chunk[:remaining] = sound[start : sound.size]
            self._sound_pos = sound.size
            return chunk

        first = sound[start : sound.size]
        remainder = end - sound.size
        repeats = remainder // sound.size
        tail = remainder % sound.size

        parts = [first]
        if repeats > 0:
            parts.extend([sound] * repeats)
        if tail > 0:
            parts.append(sound[:tail])

        self._sound_pos = tail
        return np.concatenate(parts) if len(parts) > 1 else parts[0]

    def _generate_loops(self, sample_rate: int) -> dict[str, np.ndarray]:
        n = sample_rate * self._LOOP_SECONDS
        t = np.arange(n, dtype=np.float32) / float(sample_rate)

        restaurant = self._load_sample_loop(
            _NOISE_SAMPLE_FILES[BACKGROUND_NOISE_RESTAURANT],
            sample_rate=sample_rate,
            target_samples=n,
            target_rms=1800.0,
        )
        if restaurant is None:
            restaurant = self._build_restaurant_loop(t, sample_rate)

        city = self._load_sample_loop(
            _NOISE_SAMPLE_FILES[BACKGROUND_NOISE_CITY],
            sample_rate=sample_rate,
            target_samples=n,
            target_rms=1700.0,
        )
        if city is None:
            city = self._build_cafe_loop(t, sample_rate)

        loops = {
            BACKGROUND_NOISE_RESTAURANT: restaurant,
            BACKGROUND_NOISE_CITY: city,
            BACKGROUND_NOISE_OFFICE: self._build_office_loop(t, sample_rate),
        }
        return loops

    def _load_sample_loop(
        self,
        path: Path,
        *,
        sample_rate: int,
        target_samples: int,
        target_rms: float,
    ) -> np.ndarray | None:
        if not path.exists():
            logger.warning("[VOICE_NOISE] Sample file not found, falling back: %s", path)
            return None

        try:
            with wave.open(str(path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                source_sample_rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                raw = wav_file.readframes(frames)

            if channels <= 0:
                raise ValueError(f"Invalid channel count: {channels}")

            decoded = self._decode_pcm_samples(raw, sample_width)
            if channels > 1:
                decoded = decoded.reshape(-1, channels).mean(axis=1).astype(np.float32)
            else:
                decoded = decoded.astype(np.float32)

            if decoded.size == 0:
                raise ValueError("Decoded sample is empty")

            resampled = self._resample_linear(decoded, source_sample_rate, sample_rate)
            if resampled.size >= target_samples:
                start = (resampled.size - target_samples) // 2
                clipped = resampled[start : start + target_samples]
            else:
                repeats = math.ceil(target_samples / float(resampled.size))
                clipped = np.tile(resampled, repeats)[:target_samples]

            logger.info(
                "[VOICE_NOISE] Using sample-based ambience: %s (src_sr=%s, dst_sr=%s)",
                path.name,
                source_sample_rate,
                sample_rate,
            )
            return self._to_int16(clipped, target_rms=target_rms)
        except Exception as exc:
            logger.warning(
                "[VOICE_NOISE] Failed to load sample %s, falling back to procedural: %s",
                path,
                exc,
            )
            return None

    def _decode_pcm_samples(self, raw: bytes, sample_width: int) -> np.ndarray:
        if sample_width == _PCM_WIDTH_U8:
            data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            return (data - 128.0) / 128.0
        if sample_width == _PCM_WIDTH_I16:
            return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if sample_width == _PCM_WIDTH_I24:
            bytes_24 = np.frombuffer(raw, dtype=np.uint8)
            if bytes_24.size % _PCM_WIDTH_I24 != 0:
                bytes_24 = bytes_24[: bytes_24.size - (bytes_24.size % _PCM_WIDTH_I24)]
            triples = bytes_24.reshape(-1, _PCM_WIDTH_I24)
            signed = (
                triples[:, 0].astype(np.int32)
                | (triples[:, 1].astype(np.int32) << 8)
                | (triples[:, 2].astype(np.int32) << 16)
            )
            signed = np.where(signed & 0x800000, signed - 0x1000000, signed)
            return signed.astype(np.float32) / 8388608.0
        if sample_width == _PCM_WIDTH_I32:
            return np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        raise ValueError(f"Unsupported PCM sample width: {sample_width}")

    def _resample_linear(
        self,
        signal: np.ndarray,
        source_sample_rate: int,
        target_sample_rate: int,
    ) -> np.ndarray:
        if source_sample_rate <= 0:
            raise ValueError(f"Invalid source sample rate: {source_sample_rate}")
        if source_sample_rate == target_sample_rate:
            return signal.astype(np.float32)
        if signal.size == 0:
            return signal.astype(np.float32)

        source_len = signal.size
        target_len = max(1, round(source_len * (target_sample_rate / float(source_sample_rate))))
        source_idx = np.arange(source_len, dtype=np.float32)
        target_idx = np.linspace(0, source_len - 1, num=target_len, dtype=np.float32)
        return np.interp(target_idx, source_idx, signal).astype(np.float32)

    def _build_restaurant_loop(self, t: np.ndarray, sample_rate: int) -> np.ndarray:
        rng = np.random.default_rng(7)
        n = t.size
        voice_low = self._bandpass_noise(rng, n, sample_rate, 120.0, 850.0)
        voice_mid = self._bandpass_noise(rng, n, sample_rate, 700.0, 2400.0)
        room_bed = self._bandpass_noise(rng, n, sample_rate, 40.0, 320.0)

        envelope = self._slow_envelope(rng, n, sample_rate, seconds=0.45)
        envelope = 0.42 + 0.78 * envelope

        clinks = self._harmonic_burst_train(
            rng,
            n,
            sample_rate,
            spec=HarmonicBurstSpec(
                events=24,
                burst_seconds=0.07,
                base_hz=2100.0,
                overtone_hz=3350.0,
                decay=26.0,
                jitter=0.22,
            ),
        )

        signal = (
            voice_low * 0.66 * envelope
            + voice_mid * 0.54 * envelope
            + room_bed * 0.28
            + clinks * 0.30
        )
        return self._to_int16(signal, target_rms=1800.0)

    def _build_cafe_loop(self, t: np.ndarray, sample_rate: int) -> np.ndarray:
        rng = np.random.default_rng(17)
        n = t.size
        chatter = self._bandpass_noise(rng, n, sample_rate, 180.0, 1300.0)
        room = self._bandpass_noise(rng, n, sample_rate, 60.0, 280.0)
        air = self._bandpass_noise(rng, n, sample_rate, 2200.0, 6000.0)

        chatter_env = self._slow_envelope(rng, n, sample_rate, seconds=0.6)
        chatter_env = 0.30 + 0.75 * chatter_env

        steam_bursts = self._noise_burst_train(
            rng,
            n,
            sample_rate,
            spec=NoiseBurstSpec(
                low_hz=2600.0,
                high_hz=7000.0,
                events=11,
                burst_seconds=0.5,
                decay=5.0,
                jitter=0.3,
            ),
        )
        cup_clinks = self._harmonic_burst_train(
            rng,
            n,
            sample_rate,
            spec=HarmonicBurstSpec(
                events=10,
                burst_seconds=0.05,
                base_hz=2700.0,
                overtone_hz=4100.0,
                decay=35.0,
                jitter=0.25,
            ),
        )

        signal = (
            chatter * 0.56 * chatter_env
            + room * 0.28
            + air * 0.08
            + steam_bursts * 0.30
            + cup_clinks * 0.16
        )
        return self._to_int16(signal, target_rms=1700.0)

    def _build_office_loop(self, t: np.ndarray, sample_rate: int) -> np.ndarray:
        rng = np.random.default_rng(27)
        n = t.size
        hvac = self._bandpass_noise(rng, n, sample_rate, 35.0, 220.0)
        rustle = self._bandpass_noise(rng, n, sample_rate, 700.0, 2100.0)
        low_hum = np.sin(2 * np.pi * 92.0 * t + 0.4) + 0.45 * np.sin(2 * np.pi * 184.0 * t + 1.2)
        keyboard_ticks = self._harmonic_burst_train(
            rng,
            n,
            sample_rate,
            spec=HarmonicBurstSpec(
                events=34,
                burst_seconds=0.015,
                base_hz=1600.0,
                overtone_hz=2900.0,
                decay=65.0,
                jitter=0.18,
            ),
        )

        signal = hvac * 0.66 + rustle * 0.12 + low_hum * 0.18 + keyboard_ticks * 0.12
        return self._to_int16(signal, target_rms=1500.0)

    def _bandpass_noise(
        self,
        rng: np.random.Generator,
        n: int,
        sample_rate: int,
        low_hz: float,
        high_hz: float,
    ) -> np.ndarray:
        white = rng.standard_normal(n).astype(np.float32)
        spectrum = np.fft.rfft(white)
        freqs = np.fft.rfftfreq(n, d=1.0 / float(sample_rate))
        band = (freqs >= low_hz) & (freqs <= high_hz)
        # Keep transitions soft to avoid ringing artifacts.
        taper = np.zeros_like(freqs, dtype=np.float32)
        taper[band] = 1.0
        if low_hz > 0:
            ramp = max(20.0, low_hz * 0.2)
            lo = (freqs >= low_hz - ramp) & (freqs < low_hz)
            if np.any(lo):
                taper[lo] = (freqs[lo] - (low_hz - ramp)) / ramp
        ramp_hi = max(20.0, high_hz * 0.15)
        hi = (freqs > high_hz) & (freqs <= high_hz + ramp_hi)
        if np.any(hi):
            taper[hi] = 1.0 - (freqs[hi] - high_hz) / ramp_hi

        filtered = np.fft.irfft(spectrum * taper, n=n).astype(np.float32)
        return self._normalize(filtered)

    def _slow_envelope(
        self,
        rng: np.random.Generator,
        n: int,
        sample_rate: int,
        *,
        seconds: float,
    ) -> np.ndarray:
        slow = self._smooth(
            np.abs(rng.standard_normal(n).astype(np.float32)),
            max(2, int(sample_rate * seconds)),
        )
        return self._normalize(slow, zero_center=False)

    def _noise_burst_train(
        self,
        rng: np.random.Generator,
        n: int,
        sample_rate: int,
        *,
        spec: NoiseBurstSpec,
    ) -> np.ndarray:
        burst_signal = np.zeros(n, dtype=np.float32)
        burst_len = max(32, int(sample_rate * spec.burst_seconds))
        base = self._bandpass_noise(rng, burst_len, sample_rate, spec.low_hz, spec.high_hz)
        env_t = np.arange(burst_len, dtype=np.float32) / float(sample_rate)
        env = np.exp(-spec.decay * env_t).astype(np.float32)

        for idx in self._event_positions(rng, n, spec.events, spec.jitter):
            end = min(idx + burst_len, n)
            length = end - idx
            if length <= 0:
                continue
            burst_signal[idx:end] += base[:length] * env[:length]

        return self._normalize(burst_signal)

    def _harmonic_burst_train(
        self,
        rng: np.random.Generator,
        n: int,
        sample_rate: int,
        *,
        spec: HarmonicBurstSpec,
    ) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        burst_len = max(16, int(sample_rate * spec.burst_seconds))
        t = np.arange(burst_len, dtype=np.float32) / float(sample_rate)
        env = np.exp(-spec.decay * t).astype(np.float32)

        for idx in self._event_positions(rng, n, spec.events, spec.jitter):
            phase = rng.uniform(0.0, 2.0 * np.pi)
            phase2 = rng.uniform(0.0, 2.0 * np.pi)
            tone = (
                np.sin(2.0 * np.pi * spec.base_hz * t + phase)
                + 0.55 * np.sin(2.0 * np.pi * spec.overtone_hz * t + phase2)
            ).astype(np.float32)
            burst = tone * env
            end = min(idx + burst_len, n)
            length = end - idx
            if length <= 0:
                continue
            out[idx:end] += burst[:length]

        return self._normalize(out)

    def _event_positions(
        self,
        rng: np.random.Generator,
        n: int,
        events: int,
        jitter: float,
    ) -> np.ndarray:
        if events <= 0:
            return np.array([], dtype=np.int32)
        base = np.linspace(0, n - 1, num=events, dtype=np.float32)
        offsets = rng.normal(0.0, jitter * (n / max(events, 1)), size=events).astype(np.float32)
        idx = np.clip(base + offsets, 0, n - 1).astype(np.int32)
        return np.sort(idx)

    def _smooth(self, signal: np.ndarray, window_size: int) -> np.ndarray:
        if window_size <= 1:
            return signal

        alpha = 1.0 / float(window_size)
        out = np.empty_like(signal)
        running = float(signal[0])
        out[0] = running
        for idx in range(1, signal.size):
            sample = float(signal[idx])
            running += alpha * (sample - running)
            out[idx] = running
        return out

    def _normalize(self, signal: np.ndarray, *, zero_center: bool = True) -> np.ndarray:
        if signal.size == 0:
            return signal.astype(np.float32)
        x = signal.astype(np.float32)
        if zero_center:
            x = x - float(np.mean(x))
        peak = float(np.max(np.abs(x)))
        if peak > _NORMALIZE_EPSILON:
            x = x / peak
        return x

    def _to_int16(self, signal: np.ndarray, target_rms: float) -> np.ndarray:
        centered = signal - float(np.mean(signal))
        rms = math.sqrt(float(np.mean(np.square(centered))))
        if rms > _RMS_EPSILON:
            centered = centered * (target_rms / rms)
        clipped = np.clip(centered, -7000.0, 7000.0)
        return clipped.astype(np.int16)


def create_background_noise_mixer(
    enabled: bool,
    noise_type: str | None,
    volume: float | None = None,
) -> BaseAudioMixer | None:
    """Create mixer instance when background noise is enabled."""
    if not enabled:
        return None

    resolved_type = normalize_background_noise_type(noise_type)
    resolved_volume = clamp_background_noise_volume(
        DEFAULT_BACKGROUND_NOISE_VOLUME if volume is None else volume
    )
    return ProceduralBackgroundNoiseMixer(
        default_sound=resolved_type,
        volume=resolved_volume,
        mixing=True,
        loop=True,
    )
