from __future__ import annotations

import numpy as np
import pytest
from pipecat.frames.frames import MixerEnableFrame, MixerUpdateSettingsFrame

from app.routers.voice.background_noise import (
    BACKGROUND_NOISE_CAFE_LEGACY,
    BACKGROUND_NOISE_CITY,
    BACKGROUND_NOISE_OFFICE,
    DEFAULT_BACKGROUND_NOISE_TYPE,
    DEFAULT_BACKGROUND_NOISE_VOLUME,
    clamp_background_noise_volume,
    create_background_noise_mixer,
    normalize_background_noise_type,
)

HALF_VOLUME = 0.5


def test_normalize_background_noise_type_fallback():
    assert normalize_background_noise_type("unknown") == DEFAULT_BACKGROUND_NOISE_TYPE
    assert normalize_background_noise_type(None) == DEFAULT_BACKGROUND_NOISE_TYPE
    assert normalize_background_noise_type(BACKGROUND_NOISE_CITY) == BACKGROUND_NOISE_CITY
    assert normalize_background_noise_type(BACKGROUND_NOISE_CAFE_LEGACY) == BACKGROUND_NOISE_CITY


def test_create_background_noise_mixer_disabled():
    assert create_background_noise_mixer(False, BACKGROUND_NOISE_CITY) is None


@pytest.mark.asyncio
async def test_background_noise_mixer_produces_audio_and_respects_controls():
    mixer = create_background_noise_mixer(
        enabled=True,
        noise_type=BACKGROUND_NOISE_CITY,
        volume=0.2,
    )
    assert mixer is not None

    await mixer.start(sample_rate=8000)
    silent = np.zeros(800, dtype=np.int16).tobytes()

    mixed = await mixer.mix(silent)
    assert len(mixed) == len(silent)
    assert mixed != silent

    await mixer.process_frame(MixerEnableFrame(enable=False))
    disabled = await mixer.mix(silent)
    assert disabled == silent

    await mixer.process_frame(MixerEnableFrame(enable=True))
    await mixer.process_frame(
        MixerUpdateSettingsFrame(settings={"sound": BACKGROUND_NOISE_OFFICE, "volume": 0.25})
    )
    remixed = await mixer.mix(silent)
    assert len(remixed) == len(silent)

    await mixer.stop()


def test_clamp_background_noise_volume():
    assert clamp_background_noise_volume(HALF_VOLUME) == HALF_VOLUME
    assert clamp_background_noise_volume(-1) == 0.0
    assert clamp_background_noise_volume(5) == 1.0
    assert clamp_background_noise_volume("bad") == DEFAULT_BACKGROUND_NOISE_VOLUME
