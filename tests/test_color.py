"""Colour maths."""

from __future__ import annotations

import pytest

from mbtok import color
from tests.conftest import pixels_around


def test_hex_roundtrip():
    """Parsing and formatting a hex colour returns the original string."""
    for value in ("#000000", "#ffffff", "#e6dccf", "#1b1611"):
        assert color.rgb_to_hex(color.hex_to_rgb(value)) == value


def test_short_hex_expands():
    """Three-digit hex expands each digit."""
    assert color.hex_to_rgb("#abc") == (0xAA, 0xBB, 0xCC)


def test_invalid_hex_rejected():
    """A malformed hex string raises rather than silently producing black."""
    with pytest.raises(ValueError):
        color.hex_to_rgb("#12345")


def test_delta_e_is_zero_for_identical_colors():
    """A colour is zero distance from itself."""
    assert color.delta_e((120, 90, 60), (120, 90, 60)) == pytest.approx(0.0, abs=1e-9)


def test_delta_e_orders_by_perceptual_distance():
    """A near-identical beige is closer than a saturated blue."""
    beige = (235, 228, 214)
    near = (240, 233, 220)
    blue = (20, 40, 160)
    assert color.delta_e(beige, near) < color.delta_e(beige, blue)


def test_delta_e_black_to_white_spans_the_scale():
    """Black to white is close to the nominal maximum of 100."""
    assert color.delta_e((0, 0, 0), (255, 255, 255)) == pytest.approx(100.0, abs=1.0)


def test_luminance_ordering():
    """Green contributes more luminance than blue at equal intensity."""
    assert color.luminance((0, 255, 0)) > color.luminance((0, 0, 255))


def test_hue_distance_wraps_around_the_wheel():
    """Hue distance takes the short way round."""
    assert color.hue_distance(350.0, 10.0) == pytest.approx(20.0)
    assert color.hue_distance(10.0, 350.0) == pytest.approx(20.0)


def test_kmeans_finds_planted_clusters():
    """Three well-separated colour groups produce three swatches."""
    pixels = (
        pixels_around((240, 230, 215), 60, 6, seed=1)
        + pixels_around((30, 30, 40), 60, 6, seed=2)
        + pixels_around((200, 60, 60), 60, 6, seed=3)
    )
    swatches = color.kmeans(pixels, k=3)
    assert len(swatches) == 3
    assert sum(s.weight for s in swatches) == pytest.approx(1.0, abs=1e-6)
    # Every planted centre should be matched by some swatch.
    for target in ((240, 230, 215), (30, 30, 40), (200, 60, 60)):
        assert min(color.delta_e(s.rgb, target) for s in swatches) < 12.0


def test_kmeans_is_deterministic():
    """The same pixels always yield the same palette."""
    pixels = pixels_around((150, 120, 90), 200, 40, seed=9)
    assert color.kmeans(pixels, k=4) == color.kmeans(pixels, k=4)


def test_kmeans_handles_fewer_colors_than_clusters():
    """Asking for more clusters than distinct colours does not fail."""
    swatches = color.kmeans([(10, 10, 10), (20, 20, 20)], k=5)
    assert 1 <= len(swatches) <= 2


def test_kmeans_on_empty_input():
    """No pixels means no swatches."""
    assert color.kmeans([], k=3) == []


def test_colorfulness_separates_grey_from_saturated():
    """A grey ramp is far less colourful than mixed saturated hues."""
    grey = [(value, value, value) for value in range(0, 250, 5)]
    vivid = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)] * 12
    assert color.colorfulness(grey) < 0.05
    assert color.colorfulness(vivid) > 0.5


def test_warmth_sign():
    """Amber reads warm, blue reads cool, grey reads neutral."""
    assert color.warmth([(230, 180, 90)] * 10) > 0.3
    assert color.warmth([(60, 90, 200)] * 10) < -0.3
    assert color.warmth([(128, 128, 128)] * 10) == pytest.approx(0.0, abs=1e-9)


def test_analyze_summarises_a_frame():
    """Palette statistics land in the expected ranges for a light warm frame."""
    palette = color.analyze(pixels_around((235, 225, 208), 120, 20, seed=4))
    assert 0.8 < palette.brightness <= 1.0
    assert palette.warmth > 0.0
    assert palette.swatches
    assert all(swatch.hex.startswith("#") for swatch in palette.swatches)


def test_analyze_empty_is_safe():
    """An empty frame produces an empty palette rather than an error."""
    palette = color.analyze([])
    assert palette.swatches == []
    assert palette.dominant == (128, 128, 128)


def test_palette_roundtrips_through_json():
    """A palette survives serialisation to the library index and back."""
    palette = color.analyze(pixels_around((120, 140, 160), 80, 25, seed=5))
    restored = color.Palette.from_dict(palette.to_dict())
    assert restored.hexes == palette.hexes
    assert restored.brightness == pytest.approx(palette.brightness, abs=1e-4)
    assert restored.warmth == pytest.approx(palette.warmth, abs=1e-4)


def test_hue_spread_low_for_monochrome():
    """A single-hue palette has a tight spread."""
    palette = color.analyze(pixels_around((200, 150, 100), 100, 10, seed=6))
    assert palette.hue_spread < 0.35


def test_palette_distance_prefers_matching_targets():
    """A cream palette is closer to cream targets than to navy ones."""
    palette = color.analyze(pixels_around((236, 226, 210), 100, 15, seed=7))
    cream = [(240, 232, 218), (200, 186, 168)]
    navy = [(18, 26, 54), (40, 50, 90)]
    assert color.palette_distance(palette, cream) < color.palette_distance(palette, navy)
