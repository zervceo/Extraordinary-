"""Beat detection against synthesised click tracks."""

from __future__ import annotations

import math
import random

import pytest

from mbtok import audio


def click_track(bpm: float, duration: float = 12.0, offset: float = 0.25) -> list[float]:
    """A synthetic drum track: a sustained tone plus a kick on every beat."""
    rate = audio.SAMPLE_RATE
    count = int(rate * duration)
    signal = [0.12 * math.sin(2 * math.pi * 220 * index / rate) for index in range(count)]
    period = 60.0 / bpm
    for beat in range(int(duration / period)):
        start = int((offset + beat * period) * rate)
        for step in range(int(0.05 * rate)):
            if start + step < count:
                decay = math.exp(-step / (0.01 * rate))
                signal[start + step] += 0.9 * decay * math.sin(2 * math.pi * 80 * step / rate)
    return signal


@pytest.mark.parametrize("bpm", [85, 90, 100, 110, 120, 128, 140, 150])
def test_tempo_is_recovered_accurately(bpm):
    """Detected tempo lands within one percent of the real tempo."""
    envelope = audio.onset_envelope(click_track(bpm))
    detected, confidence = audio.estimate_tempo(envelope)
    assert detected == pytest.approx(bpm, rel=0.01)
    assert confidence > 0.2


@pytest.mark.parametrize("offset", [0.0, 0.18, 0.25, 0.4])
def test_downbeat_offset_is_recovered(offset):
    """The first beat is located to within about 40 milliseconds."""
    envelope = audio.onset_envelope(click_track(120.0, offset=offset))
    detected, _ = audio.estimate_tempo(envelope)
    found = audio.estimate_offset(envelope, detected)
    period = 60.0 / detected
    error = min(abs(found - offset), abs(found - offset + period), abs(found - offset - period))
    assert error < 0.045


def test_unpulsed_audio_reports_low_confidence():
    """Noise with no beat is flagged rather than trusted."""
    generator = random.Random(0)
    noise = [generator.uniform(-0.2, 0.2) for _ in range(audio.SAMPLE_RATE * 8)]
    _, confidence = audio.estimate_tempo(audio.onset_envelope(noise))
    assert confidence < 0.2


def test_silence_produces_no_envelope():
    """Digital silence yields a flat envelope rather than spurious onsets."""
    envelope = audio.onset_envelope([0.0] * (audio.SAMPLE_RATE * 3))
    assert all(value == 0.0 for value in envelope)


def test_very_short_audio_is_handled():
    """A clip shorter than one analysis window returns nothing, not an error."""
    assert audio.onset_envelope([0.1] * 100) == []
    assert audio.estimate_tempo([]) == (100.0, 0.0)
    assert audio.estimate_offset([], 120.0) == 0.0


def test_tempo_is_folded_into_a_usable_range():
    """Octave errors are folded back to a tempo people actually cut on."""
    assert 78.0 <= audio._fold_tempo(40.0) <= 165.0
    assert 78.0 <= audio._fold_tempo(300.0) <= 165.0
    assert audio._fold_tempo(120.0) == pytest.approx(120.0)


def test_fixed_grid_needs_no_analysis():
    """A supplied tempo produces an exact grid with full confidence."""
    grid = audio.fixed_grid(120.0, offset=0.5, duration=4.0)
    assert grid.seconds_per_beat == pytest.approx(0.5)
    assert grid.confidence == 1.0
    assert grid.source == "given"
    assert grid.beats[:3] == pytest.approx([0.5, 1.0, 1.5])


def test_grid_snaps_to_the_nearest_beat():
    """Snapping moves a moment onto the grid, in either direction."""
    grid = audio.fixed_grid(120.0, offset=0.0)
    assert grid.snap(1.13) == pytest.approx(1.0)
    assert grid.snap(1.4) == pytest.approx(1.5)


def test_grid_times_can_step_by_several_beats():
    """Taking every other beat doubles the spacing."""
    grid = audio.fixed_grid(120.0)
    assert grid.times(3, every=2) == pytest.approx([0.0, 1.0, 2.0])


def test_absurd_tempo_is_clamped():
    """A nonsense BPM cannot produce a zero or negative beat length."""
    assert audio.fixed_grid(0.0).seconds_per_beat > 0
    assert audio.fixed_grid(100000.0).seconds_per_beat > 0


def test_grid_serialises():
    """The grid summary carries what the report needs."""
    data = audio.fixed_grid(104.0, offset=0.31).to_dict()
    assert data["bpm"] == pytest.approx(104.0)
    assert data["source"] == "given"
