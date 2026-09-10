"""Decide where to crop a frame that is the wrong shape for a phone.

A 16:9 photograph cropped to 9:16 keeps barely a third of its width, and a
blind centre crop throws away whichever third the subject happened to be in.
There is no face detection here and there does not need to be: the eye goes to
detail and colour, so summing edge energy and saturation across the frame finds
the interesting region well enough to be a clear improvement on guessing.

The result is deliberately pulled back toward the centre. Photographs are
mostly composed near the middle, and a confidently wrong crop at the very edge
of a frame looks far worse than a slightly lazy one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .ffmpeg import Frame
from .util import clamp

#: Output shape everything is cropped toward.
TARGET_ASPECT = 9.0 / 16.0

#: The centre bias adapts to how strong the evidence is. A frame with one
#: obvious subject gets a wide, gentle bias and is largely trusted; an evenly
#: detailed frame gets a narrow, strong one and stays near the middle, because
#: there the centre really is the best available answer.
CENTRE_SIGMA_WEAK = 0.28
CENTRE_SIGMA_STRONG = 0.75

#: Saturation's weight relative to edge energy in the interest map.
COLOUR_WEIGHT = 0.35


@dataclass(frozen=True)
class Focus:
    """Where the interesting part of a frame sits, as fractions of its size."""

    x: float = 0.5
    y: float = 0.5
    confidence: float = 0.0

    @property
    def is_centred(self) -> bool:
        """True when the focus is close enough to the middle to ignore."""
        return abs(self.x - 0.5) < 0.02 and abs(self.y - 0.5) < 0.02

    def to_dict(self) -> dict:
        """JSON representation for the library index."""
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Focus":
        """Rebuild from :meth:`to_dict` output, tolerating a missing entry."""
        if not data:
            return cls()
        return cls(
            x=float(data.get("x", 0.5)),
            y=float(data.get("y", 0.5)),
            confidence=float(data.get("confidence", 0.0)),
        )


def interest_map(frame: Frame) -> list[float]:
    """Per-pixel visual interest: edge energy plus a little colour weight.

    Edges are where detail lives, and detail is where a subject usually is. A
    smooth wall scores near zero however brightly lit it is, which is exactly
    the region a crop should be willing to discard.
    """
    columns, rows = frame.columns, frame.rows
    if columns < 2 or rows < 2:
        return [0.0] * max(0, columns * rows)

    luma = [
        0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]
        for pixel in frame.pixels[: columns * rows]
    ]
    values: list[float] = [0.0] * (columns * rows)
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column
            right = luma[index + 1] if column + 1 < columns else luma[index]
            below = luma[index + columns] if row + 1 < rows else luma[index]
            gradient = abs(luma[index] - right) + abs(luma[index] - below)

            red, green, blue = frame.pixels[index]
            highest, lowest = max(red, green, blue), min(red, green, blue)
            saturation = (highest - lowest) / highest if highest else 0.0

            values[index] = gradient + saturation * 255.0 * COLOUR_WEIGHT
    return values


def _profiles(frame: Frame) -> tuple[list[float], list[float]]:
    """Column and row totals of the interest map."""
    values = interest_map(frame)
    columns, rows = frame.columns, frame.rows
    column_totals = [0.0] * columns
    row_totals = [0.0] * rows
    for row in range(rows):
        base = row * columns
        for column in range(columns):
            value = values[base + column]
            column_totals[column] += value
            row_totals[row] += value
    return column_totals, row_totals


def _best_window(profile: Sequence[float], window: int) -> tuple[float, float]:
    """Slide a window along *profile* and return its best centre and confidence.

    The centre comes back as a fraction of the profile's length. Confidence is
    how much better the winning position is than the average one, which is near
    zero for an evenly detailed frame where the crop genuinely does not matter.
    """
    length = len(profile)
    if length == 0 or window >= length or window <= 0:
        return (0.5, 0.0)

    running = sum(profile[:window])
    scores: list[float] = [running]
    for start in range(1, length - window + 1):
        running += profile[start + window - 1] - profile[start - 1]
        scores.append(running)

    if len(scores) <= 1:
        return (0.5, 0.0)

    # How much better the best position is than an average one. This is the
    # evidence, measured before any bias is applied to it.
    average = sum(scores) / len(scores)
    peak = max(scores)
    confidence = clamp((peak / average - 1.0) if average > 0 else 0.0, 0.0, 1.0)

    if confidence <= 0.0:
        # A frame with no detail anywhere, or detail spread perfectly evenly,
        # offers no evidence at all. Ties must resolve to the middle rather
        # than to whichever window the scan happened to visit first.
        return (0.5, 0.0)

    sigma = CENTRE_SIGMA_WEAK + (CENTRE_SIGMA_STRONG - CENTRE_SIGMA_WEAK) * confidence
    weighted = [
        score * math.exp(-(((start + window / 2.0) / length - 0.5) ** 2) / (2.0 * sigma**2))
        for start, score in enumerate(scores)
    ]

    best_start = max(range(len(weighted)), key=lambda index: weighted[index])
    return (clamp((best_start + window / 2.0) / length, 0.0, 1.0), confidence)


def focus_for_frame(frame: Frame, target_aspect: float = TARGET_ASPECT) -> Focus:
    """Find the best crop centre in one frame."""
    if not frame.valid:
        return Focus()

    columns, rows = frame.columns, frame.rows
    source_aspect = columns / rows if rows else 1.0
    column_totals, row_totals = _profiles(frame)

    if source_aspect > target_aspect:
        # Too wide: the horizontal position is the decision.
        window = max(1, int(round(rows * target_aspect)))
        x, confidence = _best_window(column_totals, window)
        return Focus(x=x, y=0.5, confidence=confidence)

    if source_aspect < target_aspect:
        # Too tall: the vertical position is the decision.
        window = max(1, int(round(columns / target_aspect)))
        y, confidence = _best_window(row_totals, window)
        return Focus(x=0.5, y=y, confidence=confidence)

    return Focus()


def focus_for(frames: Sequence[Frame], target_aspect: float = TARGET_ASPECT) -> Focus:
    """Average the crop decision across several frames of a clip.

    A single frame of footage can be misleading, and a crop that moved between
    sampled frames would be a pan, which this does not attempt. Averaging keeps
    the framing fixed and sensible for the whole shot.
    """
    results = [focus_for_frame(frame, target_aspect) for frame in frames if frame.valid]
    if not results:
        return Focus()
    total_weight = sum(result.confidence for result in results)
    if total_weight <= 0:
        return Focus(confidence=0.0)
    x = sum(result.x * result.confidence for result in results) / total_weight
    y = sum(result.y * result.confidence for result in results) / total_weight
    return Focus(
        x=clamp(x, 0.0, 1.0),
        y=clamp(y, 0.0, 1.0),
        confidence=total_weight / len(results),
    )


def crop_offset(focus_fraction: float, source: int, window: int) -> int:
    """Convert a focus fraction into a pixel offset for ffmpeg's ``crop``.

    The window is centred on the focus point and then pushed back inside the
    frame, so a subject near an edge still yields a full-width crop.
    """
    if window >= source:
        return 0
    ideal = focus_fraction * source - window / 2.0
    return int(round(clamp(ideal, 0.0, float(source - window))))
