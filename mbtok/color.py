"""Colour maths used for palette extraction, mood matching and scoring.

Everything here is pure standard library so the tool works on a fresh Mac with
nothing but Python and ffmpeg installed. The inputs are always plain RGB
triples in the 0-255 range.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from typing import Sequence

RGB = tuple[int, int, int]

# sRGB -> XYZ (D65) matrix rows, used on linearised channel values.
_XYZ_MATRIX = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)
_D65_WHITE = (0.95047, 1.00000, 1.08883)


def hex_to_rgb(value: str) -> RGB:
    """Parse ``#rrggbb`` (or ``rrggbb``) into an RGB triple."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgb_to_hex(color: Sequence[float]) -> str:
    """Format an RGB triple as ``#rrggbb``."""
    red, green, blue = (int(round(max(0.0, min(255.0, channel)))) for channel in color)
    return f"#{red:02x}{green:02x}{blue:02x}"


def _linearize(channel: float) -> float:
    """Undo the sRGB transfer function for one 0-1 channel value."""
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def rgb_to_lab(color: Sequence[float]) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIE L*a*b*, which is roughly perceptually uniform."""
    linear = [_linearize(channel / 255.0) for channel in color]
    xyz = [
        sum(coefficient * value for coefficient, value in zip(row, linear))
        for row in _XYZ_MATRIX
    ]
    scaled = [component / white for component, white in zip(xyz, _D65_WHITE)]

    def pivot(value: float) -> float:
        if value > 0.008856:
            return value ** (1.0 / 3.0)
        return (7.787 * value) + (16.0 / 116.0)

    fx, fy, fz = (pivot(value) for value in scaled)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def delta_e(first: Sequence[float], second: Sequence[float]) -> float:
    """CIE76 colour difference between two RGB triples.

    Values below roughly 2.3 are indistinguishable to the eye; 100 is the
    distance between black and white.
    """
    lab_a = rgb_to_lab(first)
    lab_b = rgb_to_lab(second)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(lab_a, lab_b)))


def rgb_to_hsv(color: Sequence[float]) -> tuple[float, float, float]:
    """Convert RGB (0-255) to HSV with hue in degrees and s/v in 0-1."""
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255.0 for channel in color))
    return (hue * 360.0, saturation, value)


def hue_distance(first: float, second: float) -> float:
    """Shortest distance in degrees between two hues on the colour wheel."""
    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


def luminance(color: Sequence[float]) -> float:
    """Relative luminance in 0-1 using Rec. 709 weights."""
    red, green, blue = color
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def colorfulness(pixels: Sequence[RGB]) -> float:
    """Hasler & Susstrunk colourfulness metric, normalised to roughly 0-1.

    Around 0.15 reads as muted or neutral, 0.5 as punchy, above 0.7 as
    saturated to the point of feeling filtered.
    """
    if not pixels:
        return 0.0
    rg_values = []
    yb_values = []
    for red, green, blue in pixels:
        rg_values.append(float(red - green))
        yb_values.append(0.5 * (red + green) - float(blue))
    rg_mean, rg_std = _mean_std(rg_values)
    yb_mean, yb_std = _mean_std(yb_values)
    std_root = math.sqrt(rg_std**2 + yb_std**2)
    mean_root = math.sqrt(rg_mean**2 + yb_mean**2)
    raw = std_root + 0.3 * mean_root
    return min(1.0, raw / 150.0)


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    """Mean and population standard deviation of *values*."""
    if not values:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return (mean, math.sqrt(variance))


def warmth(pixels: Sequence[RGB]) -> float:
    """Warm/cool balance in -1 (cool blue) to +1 (warm amber)."""
    if not pixels:
        return 0.0
    total = 0.0
    for red, green, blue in pixels:
        total += (red - blue) / 255.0
    return max(-1.0, min(1.0, total / len(pixels)))


@dataclass(frozen=True)
class Swatch:
    """One colour in an extracted palette."""

    rgb: RGB
    weight: float

    @property
    def hex(self) -> str:
        """The swatch as ``#rrggbb``."""
        return rgb_to_hex(self.rgb)

    @property
    def hsv(self) -> tuple[float, float, float]:
        """The swatch in HSV (hue degrees, saturation, value)."""
        return rgb_to_hsv(self.rgb)


@dataclass
class Palette:
    """An extracted palette plus the summary statistics we score against."""

    swatches: list[Swatch] = field(default_factory=list)
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    colorfulness: float = 0.0
    warmth: float = 0.0
    hue_spread: float = 0.0

    @property
    def hexes(self) -> list[str]:
        """Palette colours as hex strings, most dominant first."""
        return [swatch.hex for swatch in self.swatches]

    @property
    def dominant(self) -> RGB:
        """The single most dominant colour, or mid grey for an empty palette."""
        return self.swatches[0].rgb if self.swatches else (128, 128, 128)

    def to_dict(self) -> dict:
        """A JSON-friendly representation for the library index."""
        return {
            "swatches": [
                {"hex": swatch.hex, "rgb": list(swatch.rgb), "weight": round(swatch.weight, 4)}
                for swatch in self.swatches
            ],
            "brightness": round(self.brightness, 4),
            "contrast": round(self.contrast, 4),
            "saturation": round(self.saturation, 4),
            "colorfulness": round(self.colorfulness, 4),
            "warmth": round(self.warmth, 4),
            "hue_spread": round(self.hue_spread, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Palette":
        """Rebuild a palette from :meth:`to_dict` output."""
        swatches = [
            Swatch(rgb=tuple(entry["rgb"]), weight=float(entry["weight"]))  # type: ignore[arg-type]
            for entry in data.get("swatches", [])
        ]
        return cls(
            swatches=swatches,
            brightness=float(data.get("brightness", 0.0)),
            contrast=float(data.get("contrast", 0.0)),
            saturation=float(data.get("saturation", 0.0)),
            colorfulness=float(data.get("colorfulness", 0.0)),
            warmth=float(data.get("warmth", 0.0)),
            hue_spread=float(data.get("hue_spread", 0.0)),
        )


def kmeans(pixels: Sequence[RGB], k: int = 5, iterations: int = 12, seed: int = 7) -> list[Swatch]:
    """Cluster *pixels* into at most *k* colours, most populous cluster first.

    Deterministic k-means++ seeding keeps the palette stable between runs, so
    re-scanning a library does not silently reshuffle every match score.
    """
    if not pixels:
        return []
    unique = list({pixel for pixel in pixels})
    k = max(1, min(k, len(unique)))

    centroids = _kmeans_plus_plus(unique, k, seed)
    assignments = [0] * len(pixels)

    for _ in range(iterations):
        moved = False
        for index, pixel in enumerate(pixels):
            best, best_distance = 0, float("inf")
            for cluster, centroid in enumerate(centroids):
                distance = _squared_distance(pixel, centroid)
                if distance < best_distance:
                    best, best_distance = cluster, distance
            if assignments[index] != best:
                assignments[index] = best
                moved = True

        sums = [[0.0, 0.0, 0.0] for _ in range(k)]
        counts = [0] * k
        for pixel, cluster in zip(pixels, assignments):
            counts[cluster] += 1
            for channel in range(3):
                sums[cluster][channel] += pixel[channel]
        for cluster in range(k):
            if counts[cluster]:
                centroids[cluster] = tuple(
                    value / counts[cluster] for value in sums[cluster]
                )  # type: ignore[assignment]
        if not moved:
            break

    total = float(len(pixels))
    swatches = [
        Swatch(
            rgb=tuple(int(round(channel)) for channel in centroids[cluster]),  # type: ignore[arg-type]
            weight=counts[cluster] / total,
        )
        for cluster in range(k)
        if counts[cluster]
    ]
    swatches.sort(key=lambda swatch: swatch.weight, reverse=True)
    return swatches


def _kmeans_plus_plus(unique: Sequence[RGB], k: int, seed: int) -> list[tuple[float, float, float]]:
    """Pick *k* well-spread starting centroids without using randomness twice."""
    import random as _random

    generator = _random.Random(seed)
    first = generator.choice(list(unique))
    centroids: list[tuple[float, float, float]] = [tuple(float(c) for c in first)]  # type: ignore[list-item]
    while len(centroids) < k:
        distances = [
            min(_squared_distance(pixel, centroid) for centroid in centroids)
            for pixel in unique
        ]
        total = sum(distances)
        if total <= 0:
            break
        target = generator.random() * total
        cumulative = 0.0
        chosen = unique[-1]
        for pixel, distance in zip(unique, distances):
            cumulative += distance
            if cumulative >= target:
                chosen = pixel
                break
        centroids.append(tuple(float(channel) for channel in chosen))  # type: ignore[arg-type]
    return centroids


def _squared_distance(first: Sequence[float], second: Sequence[float]) -> float:
    """Squared Euclidean distance in RGB space."""
    return sum((x - y) ** 2 for x, y in zip(first, second))


def analyze(pixels: Sequence[RGB], swatch_count: int = 5) -> Palette:
    """Build a :class:`Palette` (colours plus statistics) from sampled pixels."""
    if not pixels:
        return Palette()

    swatches = kmeans(pixels, k=swatch_count)
    luminances = [luminance(pixel) for pixel in pixels]
    mean_luma, luma_std = _mean_std(luminances)
    saturations = [rgb_to_hsv(pixel)[1] for pixel in pixels]
    mean_saturation = sum(saturations) / len(saturations)

    return Palette(
        swatches=swatches,
        brightness=mean_luma,
        contrast=min(1.0, luma_std * 2.5),
        saturation=mean_saturation,
        colorfulness=colorfulness(pixels),
        warmth=warmth(pixels),
        hue_spread=_hue_spread(swatches),
    )


def _hue_spread(swatches: Sequence[Swatch]) -> float:
    """How far apart the palette's hues sit, normalised to 0-1.

    Low values mean a tight, monochromatic palette, which is what most
    "aesthetic" mood boards are built on.
    """
    colored = [swatch for swatch in swatches if swatch.hsv[1] > 0.12]
    if len(colored) < 2:
        return 0.0
    hues = [swatch.hsv[0] for swatch in colored]
    worst = 0.0
    for index, hue in enumerate(hues):
        for other in hues[index + 1 :]:
            worst = max(worst, hue_distance(hue, other))
    return worst / 180.0


def palette_distance(palette: Palette, targets: Sequence[RGB]) -> float:
    """Average perceptual distance from *palette* to the nearest *targets*.

    Each palette swatch is matched to its closest target colour and the
    distances are weighted by how much of the frame that swatch covers, so a
    stray accent colour cannot drag an otherwise on-brief image out of a mood.
    """
    if not palette.swatches or not targets:
        return 100.0
    total_weight = sum(swatch.weight for swatch in palette.swatches) or 1.0
    total = 0.0
    for swatch in palette.swatches:
        nearest = min(delta_e(swatch.rgb, target) for target in targets)
        total += nearest * swatch.weight
    return total / total_weight


def blend(first: Sequence[float], second: Sequence[float], amount: float) -> RGB:
    """Mix two colours, where *amount* 0 returns *first* and 1 returns *second*."""
    return tuple(  # type: ignore[return-value]
        int(round(a + (b - a) * amount)) for a, b in zip(first, second)
    )
