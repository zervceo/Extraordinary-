"""Text measurement, wrapping and shrink-to-fit."""

from __future__ import annotations

import pytest

from mbtok import presets, typeset

FRAME = 1080
LIMIT = FRAME * typeset.SAFE_WIDTH


def test_measure_grows_with_length_and_size():
    """Width scales with both character count and font size."""
    assert typeset.measure("aa", 40) > typeset.measure("a", 40)
    assert typeset.measure("hello", 80) == pytest.approx(typeset.measure("hello", 40) * 2)


def test_wide_letters_measure_wider():
    """An M is wider than an i at the same size."""
    assert typeset.advance("M") > typeset.advance("i")
    assert typeset.measure("MMMM", 50) > typeset.measure("iiii", 50)


def test_short_text_is_left_on_one_line():
    """Text that already fits is not wrapped or shrunk."""
    fitted = typeset.fit("linen weather", 62, FRAME)
    assert fitted.line_count == 1
    assert fitted.size == 62


def test_long_text_wraps_before_it_shrinks():
    """Wrapping is preferred to smaller type."""
    fitted = typeset.fit("Studying Like The Semester Owes Me Money", 67, FRAME)
    assert fitted.line_count > 1
    assert fitted.size == 67


@pytest.mark.parametrize(
    "text",
    [
        "3AM IN A CITY THAT NEVER SITS DOWN",
        "BRINGING THIS BACK, SORRY NOT SORRY",
        "Studying Like The Semester Owes Me Money",
        "a genuinely absurd hook that goes on and on and refuses to stop at all",
        "SUPERCALIFRAGILISTICEXPIALIDOCIOUSSUPERCALIFRAGILISTIC",
    ],
)
def test_everything_fits_within_the_safe_width(text):
    """No input produces a line wider than the safe area."""
    for tracking in (0, 4, 6, 9):
        fitted = typeset.fit(text, 68, FRAME, tracking=tracking)
        assert fitted.width <= LIMIT + 1e-6


@pytest.mark.parametrize("key", presets.names())
def test_every_preset_hook_fits(key):
    """Every shipped hook fits its own mood's typography."""
    preset = presets.get(key)
    style = preset.typography
    nominal = FRAME * style.size_ratio
    for hook in preset.copy.hooks:
        fitted = typeset.fit(hook, nominal, FRAME, tracking=style.letter_spacing)
        assert fitted.width <= LIMIT + 1e-6
        assert fitted.line_count <= 3
        assert fitted.size >= nominal * typeset.MIN_SCALE - 1


def test_tracking_below_threshold_changes_nothing():
    """Subtle tracking values leave the text untouched."""
    assert typeset.track("hello there", 3) == "hello there"


def test_tracking_spaces_letters_and_keeps_words_apart():
    """Tracked text separates letters but still reads as separate words."""
    tracked = typeset.track("go now", 7)
    assert tracked.startswith("g o")
    assert typeset.TRACKED_WORD_GAP in tracked
    assert "".join(tracked.split()) == "gonow"


def test_tracked_wrapping_preserves_word_boundaries():
    """Wrapping tracked text does not merge words together."""
    fitted = typeset.fit("SHOT ON A WALK, NOTHING PLANNED", 58, FRAME, tracking=9)
    rebuilt = "".join("".join(line.split()) for line in fitted.lines)
    assert rebuilt == "SHOTONAWALK,NOTHINGPLANNED"
    assert fitted.line_count > 1


def test_wrapping_is_preferred_to_breaking_a_word():
    """Ordinary long text wraps between words, never inside them."""
    fitted = typeset.fit("everything soft and nothing at all loud today", 66, FRAME)
    assert fitted.line_count > 1
    assert all(" " in line or line.isalpha() for line in fitted.lines)
    rejoined = " ".join(fitted.lines)
    assert rejoined == "everything soft and nothing at all loud today"


def test_single_oversized_word_is_broken_rather_than_clipped():
    """One unbroken run wider than the frame is split across lines.

    Breaking mid-word is the last resort, but text running off the side of the
    screen is the one outcome with no recovery.
    """
    word = "A" * 40
    fitted = typeset.fit(word, 70, FRAME)
    assert fitted.line_count > 1
    assert "".join(fitted.lines) == word
    assert fitted.width <= LIMIT + 1e-6


def test_size_never_falls_below_the_floor():
    """Shrinking stops at the readable minimum rather than vanishing."""
    fitted = typeset.fit("X" * 400, 70, FRAME, min_scale=0.62)
    assert fitted.size >= int(70 * 0.62) - 1


def test_empty_text_is_handled():
    """Empty or whitespace input produces no lines."""
    for text in ("", "   ", "\n\t"):
        fitted = typeset.fit(text, 60, FRAME)
        assert fitted.lines == []
        assert fitted.text == ""


def test_whitespace_is_normalised():
    """Ragged spacing in a hook does not survive into the render."""
    fitted = typeset.fit("  too    many\n\nspaces  ", 60, FRAME)
    assert fitted.text == "too many spaces"


def test_max_lines_is_respected():
    """The layout never exceeds the requested number of lines."""
    fitted = typeset.fit("one two three four five six seven eight nine ten", 64, FRAME, max_lines=2)
    assert fitted.line_count <= 2
