"""Fit overlay text to the frame without a font-metrics library.

``drawtext`` neither wraps nor shrinks: a hook wider than the frame is simply
clipped at both edges, which is exactly the kind of error that only shows up
after the video is rendered. So text width is estimated here from a table of
per-character advances, and the text is wrapped and, if it still does not fit,
scaled down until it does.

The estimate is approximate by design. It is calibrated a little wide, so the
failure mode is text slightly smaller than it could be rather than text running
off the side of the screen.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Character advance widths in thousandths of an em, following the proportions
#: of a typical humanist sans. Serif and geometric faces differ, but not by
#: enough to matter once the safety margin is applied.
_ADVANCES = {
    " ": 300, "\t": 300,
    "i": 240, "j": 260, "l": 240, "t": 330, "f": 330, "r": 350, "I": 290,
    "m": 850, "w": 740, "M": 880, "W": 920,
    ".": 290, ",": 290, ":": 290, ";": 290, "'": 220, "!": 300, "|": 260,
    "\"": 380, "(": 340, ")": 340, "-": 360, "?": 500,
}
_DEFAULT_LOWER = 545
_DEFAULT_UPPER = 680
_DEFAULT_DIGIT = 560

#: Fraction of the frame width the text may occupy. The remainder keeps the
#: line clear of the action buttons that sit down the right-hand side.
SAFE_WIDTH = 0.84

#: How small the text may be scaled before wrapping harder is preferred.
MIN_SCALE = 0.62


def advance(character: str) -> int:
    """Advance width of one character, in thousandths of an em."""
    if character in _ADVANCES:
        return _ADVANCES[character]
    if character.isdigit():
        return _DEFAULT_DIGIT
    if character.isupper():
        return _DEFAULT_UPPER
    if character.islower():
        return _DEFAULT_LOWER
    return _DEFAULT_LOWER


def measure(text: str, size: float) -> float:
    """Estimated rendered width of *text* in pixels at font *size*."""
    return sum(advance(character) for character in text) * size / 1000.0


#: Tracking at or above this level is rendered as spaced-out display type.
TRACKING_THRESHOLD = 5

#: Spaces inserted between words in tracked text, so word boundaries survive.
TRACKED_WORD_GAP = "   "


def track(text: str, level: int) -> str:
    """Emulate wide letter-spacing, which ``drawtext`` cannot do natively.

    Only display-scale tracking is worth faking; below the threshold the text
    is returned unchanged rather than mangled into something unreadable.
    """
    if level < TRACKING_THRESHOLD or not text.strip():
        return text
    words = [word for word in text.split() if word]
    return TRACKED_WORD_GAP.join(" ".join(word) for word in words)


def wrap(
    text: str,
    size: float,
    max_width: float,
    max_lines: int = 3,
    tracking: int = 0,
) -> list[str]:
    """Greedily wrap *text* so each line fits in *max_width* once tracked.

    Lines are broken on the untracked text but measured with tracking applied,
    because tracking is what actually decides whether a line fits, and it is
    applied per line rather than to the whole string.

    A word longer than the whole line is left alone rather than hyphenated:
    breaking a word mid-way looks like a bug, and the caller will scale the
    text down instead.
    """
    words: list[str] = []
    for word in text.split():
        words.extend(_break_oversized(word, size, max_width, tracking))
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and measure(track(candidate, tracking), size) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return lines

    # Too many lines: fold the overflow back onto the last allowed line and let
    # the caller shrink the type to make it fit.
    kept = lines[: max_lines - 1]
    kept.append(" ".join(lines[max_lines - 1 :]))
    return kept


def _break_oversized(word: str, size: float, max_width: float, tracking: int) -> list[str]:
    """Split a word that cannot fit on a line of its own.

    Breaking a word mid-way is ugly, so it is the last resort, reached only
    when a single unbroken run of characters is wider than the frame even after
    the type has been scaled down. Clipping it off the side of the screen is
    the one outcome that is worse.
    """
    if measure(track(word, tracking), size) <= max_width:
        return [word]

    pieces: list[str] = []
    current = ""
    for character in word:
        candidate = current + character
        if current and measure(track(candidate, tracking), size) > max_width:
            pieces.append(current)
            current = character
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [word]


@dataclass
class FittedText:
    """Text laid out to fit the frame, with the size it needs."""

    lines: list[str]
    size: int
    width: float

    @property
    def text(self) -> str:
        """The wrapped text, newline separated."""
        return "\n".join(self.lines)

    @property
    def line_count(self) -> int:
        """How many lines the text occupies."""
        return len(self.lines)


def fit(
    text: str,
    nominal_size: float,
    frame_width: int,
    max_lines: int = 3,
    tracking: int = 0,
    safe_width: float = SAFE_WIDTH,
    min_scale: float = MIN_SCALE,
) -> FittedText:
    """Wrap and, if needed, shrink *text* until it fits inside the frame.

    Wrapping is tried first at full size, because smaller type on one line is
    almost always worse than the same type across two.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return FittedText(lines=[], size=int(nominal_size), width=0.0)

    max_width = frame_width * safe_width
    size = float(nominal_size)
    floor = max(14.0, nominal_size * min_scale)

    while True:
        lines = [
            track(line, tracking)
            for line in wrap(cleaned, size, max_width, max_lines=max_lines, tracking=tracking)
        ]
        widest = max((measure(line, size) for line in lines), default=0.0)
        if widest <= max_width or size <= floor:
            return FittedText(lines=lines, size=int(round(size)), width=widest)
        # Scale straight to the size that would fit, with a little margin, so
        # this converges in one or two passes instead of creeping down.
        size = max(floor, size * (max_width / widest) * 0.98)
