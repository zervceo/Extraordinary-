"""Content-aware crop placement."""

from __future__ import annotations

import pytest

from mbtok import crop
from mbtok.ffmpeg import Frame
from mbtok.render import cover_scale, crop_position


def frame_with_subject(
    columns: int,
    rows: int,
    subject_centre: float,
    subject_width: float = 0.18,
    axis: str = "x",
) -> Frame:
    """A smooth frame with one detailed, saturated block in it.

    The background is a gentle gradient with no edges; the subject is a
    checkerboard, which is all edge. That is the signal the detector looks for.
    """
    pixels = []
    low = subject_centre - subject_width / 2
    high = subject_centre + subject_width / 2
    for row in range(rows):
        for column in range(columns):
            position = (column / columns) if axis == "x" else (row / rows)
            if low <= position <= high:
                on = (column + row) % 2 == 0
                pixels.append((220, 40, 30) if on else (20, 30, 90))
            else:
                shade = 200 + int(20 * column / max(1, columns))
                pixels.append((shade, shade, shade))
    return Frame(pixels=pixels, columns=columns, rows=rows)


def flat_frame(columns: int, rows: int) -> Frame:
    """A frame with no detail anywhere."""
    return Frame(pixels=[(180, 180, 180)] * (columns * rows), columns=columns, rows=rows)


@pytest.mark.parametrize("planted", [0.15, 0.3, 0.5, 0.7, 0.85])
def test_horizontal_subject_is_found(planted):
    """A detailed region in a wide frame pulls the focus toward it."""
    focus = crop.focus_for_frame(frame_with_subject(64, 36, planted))
    assert abs(focus.x - planted) < 0.14
    assert focus.confidence > 0.1


def test_focus_moves_in_the_right_direction():
    """A left-hand subject and a right-hand one land on opposite sides."""
    left = crop.focus_for_frame(frame_with_subject(64, 36, 0.15))
    right = crop.focus_for_frame(frame_with_subject(64, 36, 0.85))
    assert left.x < 0.4 < 0.6 < right.x


def test_vertical_subject_is_found_in_a_tall_frame():
    """A very tall frame is cropped vertically, toward the subject."""
    frame = frame_with_subject(64, 240, 0.2, subject_width=0.12, axis="y")
    focus = crop.focus_for_frame(frame)
    assert focus.y < 0.45
    assert focus.x == 0.5


def test_flat_frame_stays_centred():
    """With no detail anywhere, the centre is the right answer."""
    focus = crop.focus_for_frame(flat_frame(64, 36))
    assert focus.is_centred
    assert focus.confidence < 0.1


def test_already_vertical_frame_needs_no_crop():
    """A 9:16 source is left alone on both axes."""
    focus = crop.focus_for_frame(frame_with_subject(36, 64, 0.2))
    assert focus.is_centred


def test_confidence_reflects_how_obvious_the_subject_is():
    """One clear subject scores higher than detail spread everywhere."""
    obvious = crop.focus_for_frame(frame_with_subject(64, 36, 0.2, subject_width=0.12))
    everywhere = crop.focus_for_frame(frame_with_subject(64, 36, 0.5, subject_width=0.95))
    assert obvious.confidence > everywhere.confidence


def test_strong_evidence_is_trusted_more_than_weak():
    """A confident detection is allowed further from the centre."""
    strong = crop.focus_for_frame(frame_with_subject(64, 36, 0.85, subject_width=0.12))
    weak = crop.focus_for_frame(frame_with_subject(64, 36, 0.85, subject_width=0.75))
    assert strong.x > weak.x


def test_focus_never_leaves_the_frame():
    """No input produces a focus outside 0-1."""
    for centre in (0.0, 0.02, 0.5, 0.98, 1.0):
        focus = crop.focus_for_frame(frame_with_subject(64, 36, centre))
        assert 0.0 <= focus.x <= 1.0
        assert 0.0 <= focus.y <= 1.0


def test_invalid_frames_are_ignored():
    """A malformed or tiny buffer yields a centred focus, not an error."""
    assert crop.focus_for_frame(Frame(pixels=[], columns=0, rows=0)).is_centred
    assert crop.focus_for_frame(Frame(pixels=[(1, 2, 3)], columns=1, rows=1)).is_centred
    assert crop.focus_for([]).is_centred


def test_frames_are_averaged_by_confidence():
    """Several frames of a clip settle on one fixed framing."""
    frames = [frame_with_subject(64, 36, centre) for centre in (0.2, 0.25, 0.22)]
    focus = crop.focus_for(frames)
    assert 0.15 < focus.x < 0.45


def test_a_confident_frame_outweighs_an_empty_one():
    """A flat frame does not drag a clear detection back to the middle."""
    focus = crop.focus_for([frame_with_subject(64, 36, 0.2), flat_frame(64, 36)])
    assert focus.x < 0.45


def test_focus_roundtrips_through_json():
    """The focus point survives being written to the library index."""
    focus = crop.Focus(x=0.23, y=0.5, confidence=0.8)
    restored = crop.Focus.from_dict(focus.to_dict())
    assert restored.x == pytest.approx(0.23)
    assert restored.confidence == pytest.approx(0.8)
    assert crop.Focus.from_dict(None).is_centred


def test_pixel_offset_is_clamped_inside_the_frame():
    """A subject near an edge still yields a full-width crop."""
    assert crop.crop_offset(0.0, 1920, 608) == 0
    assert crop.crop_offset(1.0, 1920, 608) == 1920 - 608
    assert crop.crop_offset(0.5, 1920, 608) == pytest.approx(656, abs=1)
    assert crop.crop_offset(0.5, 1080, 1080) == 0


# -- the renderer's use of it --------------------------------------------

def test_crop_position_picks_the_axis_that_needs_cropping():
    """Wide sources shift horizontally, tall ones vertically."""
    confident = crop.Focus(x=0.2, y=0.2, confidence=0.9)
    assert crop_position(confident, 1920 / 1080, 9 / 16)[0] == "x"
    assert crop_position(confident, 1080 / 4000, 9 / 16)[0] == "y"


def test_matching_aspect_needs_no_crop():
    """A source already the output shape is never offset."""
    assert crop_position(crop.Focus(x=0.2, confidence=0.9), 9 / 16, 9 / 16) is None


def test_low_confidence_falls_back_to_centred():
    """Weak evidence is ignored rather than acted on."""
    assert crop_position(crop.Focus(x=0.2, confidence=0.01), 1920 / 1080, 9 / 16) is None
    assert "x=(iw-ow)" not in cover_scale(1080, 1920, crop.Focus(x=0.2, confidence=0.01), 1.778)


def test_missing_focus_is_safe():
    """No focus and no aspect both degrade to a centred crop."""
    assert crop_position(None, 1.778, 0.5625) is None
    assert crop_position(crop.Focus(x=0.2, confidence=0.9), 0.0, 0.5625) is None
    assert cover_scale(1080, 1920).endswith("crop=1080:1920")


def test_crop_offset_fraction_stays_in_range():
    """The emitted fraction of the available slack is always valid."""
    for x in (0.0, 0.1, 0.9, 1.0):
        axis, fraction = crop_position(crop.Focus(x=x, confidence=0.9), 1920 / 1080, 9 / 16)
        assert axis == "x"
        assert 0.0 <= fraction <= 1.0


def test_an_exactly_centred_focus_needs_no_offset():
    """A focus already in the middle produces a plain centred crop."""
    assert crop_position(crop.Focus(x=0.5, y=0.5, confidence=0.9), 1920 / 1080, 9 / 16) is None


def test_left_subject_crops_left_and_right_crops_right():
    """The emitted offset moves the window the way the subject sits."""
    _, left = crop_position(crop.Focus(x=0.15, confidence=0.9), 1920 / 1080, 9 / 16)
    _, right = crop_position(crop.Focus(x=0.85, confidence=0.9), 1920 / 1080, 9 / 16)
    assert left < 0.3 < 0.7 < right
