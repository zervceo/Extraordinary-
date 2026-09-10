"""Beat grid derivation, so cuts land where the music expects them.

There is no numpy dependency here. Onset strength comes from a short-window
energy flux over a mel-ish set of frequency bands, and tempo comes from
autocorrelating that envelope. It is not as sharp as a dedicated MIR library,
but for the four-on-the-floor loops that soundtrack this kind of video it lands
within a few milliseconds, and ``--bpm`` is always there when it does not.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import ffmpeg as ff
from .util import clamp

log = logging.getLogger("mbtok.audio")

SAMPLE_RATE = 22050
HOP = 512
WINDOW = 1024

#: Tempo search range. Below 60 the grid is too sparse to cut on, above 180
#: the detector starts locking onto hi-hats instead of the pulse.
MIN_BPM = 60.0
MAX_BPM = 180.0


@dataclass
class BeatGrid:
    """A tempo, a starting offset, and the beat times derived from them."""

    bpm: float
    offset: float = 0.0
    duration: float = 0.0
    confidence: float = 0.0
    source: str = "estimated"
    beats: list[float] = field(default_factory=list)

    @property
    def seconds_per_beat(self) -> float:
        """Length of one beat in seconds."""
        return 60.0 / self.bpm if self.bpm > 0 else 0.5

    def times(self, count: int, every: int = 1, start_index: int = 0) -> list[float]:
        """Return *count* beat times, taking every *every*-th beat."""
        step = max(1, every)
        return [
            self.offset + (start_index + index * step) * self.seconds_per_beat
            for index in range(count)
        ]

    def snap(self, moment: float) -> float:
        """Move *moment* to the nearest beat."""
        if self.bpm <= 0:
            return moment
        beats_from_start = (moment - self.offset) / self.seconds_per_beat
        return self.offset + round(beats_from_start) * self.seconds_per_beat

    def to_dict(self) -> dict:
        """JSON-friendly summary for reports."""
        return {
            "bpm": round(self.bpm, 2),
            "offset": round(self.offset, 4),
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "seconds_per_beat": round(self.seconds_per_beat, 4),
        }


def fixed_grid(bpm: float, offset: float = 0.0, duration: float = 0.0) -> BeatGrid:
    """A grid from a known tempo, with no analysis at all."""
    tempo = clamp(bpm, 20.0, 300.0)
    grid = BeatGrid(bpm=tempo, offset=offset, duration=duration, confidence=1.0, source="given")
    if duration > 0:
        count = int(duration / grid.seconds_per_beat) + 1
        grid.beats = grid.times(count)
    return grid


def analyze_track(
    path: Path,
    binaries: ff.Binaries | None = None,
    bpm: float | None = None,
    max_seconds: float = 90.0,
) -> BeatGrid:
    """Derive a :class:`BeatGrid` for an audio file.

    A caller-supplied *bpm* still gets an onset pass, because knowing the tempo
    does not tell you where the first downbeat falls, and a grid that is right
    but offset is worse than no grid at all.
    """
    binaries = binaries or ff.find_binaries()
    samples = ff.decode_audio_mono(binaries, path, SAMPLE_RATE, max_seconds=max_seconds)
    if not samples:
        log.warning("no audio decoded from %s; falling back to 100 BPM", path)
        return fixed_grid(bpm or 100.0)

    duration = len(samples) / SAMPLE_RATE
    envelope = onset_envelope(samples)
    if not envelope:
        return fixed_grid(bpm or 100.0, duration=duration)

    if bpm and bpm > 0:
        tempo, confidence = float(bpm), 1.0
        source = "given"
    else:
        tempo, confidence = estimate_tempo(envelope)
        source = "estimated"

    offset = estimate_offset(envelope, tempo)
    grid = BeatGrid(
        bpm=tempo, offset=offset, duration=duration, confidence=confidence, source=source
    )
    count = int(duration / grid.seconds_per_beat) + 1
    grid.beats = [time for time in grid.times(count) if time <= duration]
    return grid


def onset_envelope(samples: Sequence[float], bands: int = 8) -> list[float]:
    """Per-frame onset strength: summed positive energy change across bands.

    Splitting the spectrum into bands before differencing is what makes a kick
    drum register even while a sustained pad holds steady behind it.
    """
    frame_count = max(0, (len(samples) - WINDOW) // HOP + 1)
    if frame_count < 4:
        return []

    window = _hann(WINDOW)
    previous: list[float] | None = None
    envelope: list[float] = []

    for index in range(frame_count):
        start = index * HOP
        frame = [samples[start + n] * window[n] for n in range(WINDOW)]
        spectrum = _band_energies(frame, bands)
        if previous is not None:
            flux = sum(max(0.0, now - before) for now, before in zip(spectrum, previous))
            envelope.append(flux)
        previous = spectrum

    return _normalize(_smooth(envelope, 3))


def _hann(size: int) -> list[float]:
    """A Hann window of *size* samples."""
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * n / (size - 1)) for n in range(size)]


def _band_energies(frame: Sequence[float], bands: int) -> list[float]:
    """Log energy per frequency band, via a Goertzel-style band filter.

    A full FFT in pure Python is slow enough to be felt on every render, and a
    handful of bands is all the onset detector actually reads.
    """
    size = len(frame)
    energies: list[float] = []
    # Band centres rise geometrically, mimicking how hearing spaces pitch.
    for band in range(bands):
        low = 40.0 * (2.4 ** band)
        high = 40.0 * (2.4 ** (band + 1))
        centre = math.sqrt(low * high)
        bin_index = centre * size / SAMPLE_RATE
        if bin_index >= size / 2:
            energies.append(0.0)
            continue
        energies.append(math.log1p(_goertzel(frame, bin_index) * 1000.0))
    return energies


def _goertzel(frame: Sequence[float], bin_index: float) -> float:
    """Energy at one (possibly fractional) DFT bin, in O(n)."""
    size = len(frame)
    omega = 2.0 * math.pi * bin_index / size
    coefficient = 2.0 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for sample in frame:
        s = sample + coefficient * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    power = s_prev2 * s_prev2 + s_prev * s_prev - coefficient * s_prev * s_prev2
    return max(0.0, power) / size


def _smooth(values: Sequence[float], radius: int) -> list[float]:
    """Moving average, to stop single-frame spikes reading as onsets."""
    if radius < 1 or not values:
        return list(values)
    result = []
    for index in range(len(values)):
        low = max(0, index - radius)
        high = min(len(values), index + radius + 1)
        window = values[low:high]
        result.append(sum(window) / len(window))
    return result


def _normalize(values: Sequence[float]) -> list[float]:
    """Scale to 0-1 and subtract the local floor so quiet passages still count."""
    if not values:
        return []
    lowest = min(values)
    highest = max(values)
    span = highest - lowest
    if span <= 1e-9:
        return [0.0] * len(values)
    return [(value - lowest) / span for value in values]


def estimate_tempo(envelope: Sequence[float]) -> tuple[float, float]:
    """Estimate BPM by autocorrelating the onset envelope.

    Returns the tempo and a 0-1 confidence: the height of the winning peak
    relative to the average correlation, which is low for spoken word or
    ambient beds where there is no steady pulse to find.
    """
    if len(envelope) < 16:
        return (100.0, 0.0)

    frames_per_second = SAMPLE_RATE / HOP
    min_lag = max(2, int(frames_per_second * 60.0 / MAX_BPM))
    max_lag = min(len(envelope) - 2, int(frames_per_second * 60.0 / MIN_BPM))
    if max_lag <= min_lag:
        return (100.0, 0.0)

    mean = sum(envelope) / len(envelope)
    centred = [value - mean for value in envelope]

    scores: dict[int, float] = {}
    for lag in range(min_lag, max_lag + 1):
        overlap = len(centred) - lag
        total = sum(centred[index] * centred[index + lag] for index in range(overlap))
        # Longer lags have fewer overlapping samples; normalise so slow tempos
        # are not handed an automatic advantage.
        scores[lag] = total / overlap

    # Reinforce each lag with its own harmonics so a detector locking onto the
    # eighth-note grid still resolves to the quarter-note pulse.
    reinforced = {
        lag: value + 0.5 * scores.get(lag * 2, 0.0) + 0.25 * scores.get(lag * 3, 0.0)
        for lag, value in scores.items()
    }
    best_lag = max(reinforced, key=lambda lag: reinforced[lag])
    best_value = reinforced[best_lag]
    average = sum(abs(value) for value in reinforced.values()) / len(reinforced)
    confidence = clamp((best_value / average - 1.0) / 3.0 if average > 0 else 0.0, 0.0, 1.0)

    # One frame of lag is worth several BPM at these tempos, so interpolate the
    # true peak position from its neighbours instead of trusting the integer.
    # Refine against the raw correlation, not the harmonic-reinforced copy:
    # reinforcement is what resolves the octave, but it samples harmonics at
    # integer multiples and so skews the shape of the peak it sits on.
    refined_lag = _refine_peak(scores, best_lag, min_lag, max_lag)
    bpm = 60.0 * frames_per_second / refined_lag
    bpm = _fold_tempo(bpm)
    return (bpm, confidence)


def _refine_peak(scores: dict[int, float], peak: int, low: int, high: int) -> float:
    """Sub-frame peak position by fitting a parabola through three points.

    Returns a fractional lag. Falls back to the integer peak when it sits at
    the edge of the search range or the three points do not form a peak.
    """
    if peak <= low or peak >= high:
        return float(peak)
    before = scores.get(peak - 1, 0.0)
    centre = scores.get(peak, 0.0)
    after = scores.get(peak + 1, 0.0)
    denominator = before - 2.0 * centre + after
    if abs(denominator) < 1e-12:
        return float(peak)
    shift = 0.5 * (before - after) / denominator
    if not -1.0 < shift < 1.0:
        return float(peak)
    return float(peak) + shift


def _fold_tempo(bpm: float) -> float:
    """Fold an octave-confused tempo into the range people actually cut on."""
    tempo = bpm
    while tempo < 78.0:
        tempo *= 2.0
    while tempo > 165.0:
        tempo /= 2.0
    return clamp(tempo, MIN_BPM, MAX_BPM)


def estimate_offset(envelope: Sequence[float], bpm: float) -> float:
    """Find where the first beat lands, by phase-testing the grid.

    The tempo says how far apart beats are; this says where to start counting.
    Every candidate phase within one beat is scored by how much onset energy
    sits on its grid lines, and the best-aligned phase wins.
    """
    if not envelope or bpm <= 0:
        return 0.0

    frames_per_second = SAMPLE_RATE / HOP
    period_frames = 60.0 / bpm * frames_per_second
    if period_frames < 2:
        return 0.0

    # Search well below one-frame resolution: being 20ms early on a cut is
    # audible, and the phase sweep is cheap enough to over-sample.
    steps = max(16, int(period_frames * 4))
    best_phase = 0.0
    best_energy = -1.0
    for step in range(steps):
        phase = step * period_frames / steps
        position = phase
        energy = 0.0
        hits = 0
        while position < len(envelope):
            energy += envelope[int(position)]
            hits += 1
            position += period_frames
        if hits and (energy / hits) > best_energy:
            best_energy = energy / hits
            best_phase = phase

    return best_phase / frames_per_second


def default_grid(duration: float = 0.0) -> BeatGrid:
    """The grid used when there is no music at all: a calm 100 BPM."""
    return fixed_grid(100.0, duration=duration)
