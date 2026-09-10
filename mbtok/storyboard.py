"""Turn a curated selection into timed shots on a beat grid.

The timing model is worth stating precisely, because the renderer depends on
it. Every cut is a *perceived* moment on the beat. A crossfade is centred on
that moment, so it starts half a transition early and ends half a transition
late. Each shot therefore has to supply a little more footage than its visible
span: half a transition at each end where it overlaps its neighbours.

    shot i visible span : cut[i-1] .. cut[i]
    shot i real length  : (cut[i] - cut[i-1]) + trans[i-1]/2 + trans[i]/2

Chaining those with ffmpeg's ``xfade`` reproduces the intended timeline exactly,
which is checked in the tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from .audio import BeatGrid
from .curate import ScoredAsset
from .library import Asset
from .presets import Preset
from .util import clamp


@dataclass
class Shot:
    """One clip on the timeline, with everything the renderer needs."""

    asset: Asset
    index: int
    start: float           # absolute time of this shot's visible span start
    visible: float         # length of the visible span
    length: float          # real media length, including transition overlaps
    source_in: float = 0.0     # seek point inside a video source
    speed: float = 1.0         # playback rate applied to video sources
    motion: str = "push_in"    # Ken Burns move for stills
    transition_in: float = 0.0
    transition_out: float = 0.0
    transition_name: str = "fade"

    @property
    def end(self) -> float:
        """Absolute time at which this shot's visible span ends."""
        return self.start + self.visible

    @property
    def is_video(self) -> bool:
        """True when the source is footage rather than a still."""
        return self.asset.kind == "video"

    def to_dict(self) -> dict:
        """JSON representation for the shot list in the report."""
        return {
            "index": self.index,
            "path": self.asset.path,
            "kind": self.asset.kind,
            "source": self.asset.source,
            "start": round(self.start, 3),
            "visible": round(self.visible, 3),
            "length": round(self.length, 3),
            "source_in": round(self.source_in, 3),
            "speed": round(self.speed, 3),
            "motion": self.motion,
            "transition": self.transition_name,
            "transition_out": round(self.transition_out, 3),
        }


@dataclass
class Storyboard:
    """A complete plan for one video."""

    shots: list[Shot] = field(default_factory=list)
    preset_key: str = ""
    duration: float = 0.0
    grid: BeatGrid | None = None
    width: int = 1080
    height: int = 1920
    fps: int = 30
    hook: str = ""
    signoff: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def assets(self) -> list[str]:
        """Paths of every asset used, in order."""
        return [shot.asset.path for shot in self.shots]

    def to_dict(self) -> dict:
        """JSON representation for the sidecar report."""
        return {
            "preset": self.preset_key,
            "duration": round(self.duration, 3),
            "size": [self.width, self.height],
            "fps": self.fps,
            "hook": self.hook,
            "signoff": self.signoff,
            "beat_grid": self.grid.to_dict() if self.grid else None,
            "shots": [shot.to_dict() for shot in self.shots],
            "warnings": self.warnings,
        }


def suggest_shot_count(preset: Preset, grid: BeatGrid, duration: float) -> int:
    """How many shots a preset wants in *duration* seconds at this tempo."""
    options = [max(1, int(beats)) for beats in preset.pacing.beats_per_shot] or [2]
    average_beats = sum(options) / len(options)
    shot_seconds = average_beats * grid.seconds_per_beat
    shot_seconds = clamp(shot_seconds, preset.pacing.min_shot, preset.pacing.max_shot)
    return max(3, int(round(duration / shot_seconds)))


def allocate_beats(total_beats: int, shot_count: int, preset: Preset, seed: int = 0) -> list[int]:
    """Split *total_beats* across *shot_count* shots using the preset's pacing.

    Working in whole beats is the point: every cut then lands exactly on the
    grid instead of near it. Shots are first given the preset's shortest
    allowed length, then upgraded toward its longer options, with the opening
    and closing shots upgraded first so the board has somewhere to breathe.
    """
    shot_count = max(1, shot_count)
    options = sorted({max(1, int(beats)) for beats in preset.pacing.beats_per_shot}) or [2]
    order = _anchor_first_order(shot_count, seed)

    smallest = options[0]
    allocation = [smallest] * shot_count

    if sum(allocation) > total_beats:
        # More shots than the tempo has room for: split the beats as evenly as
        # whole numbers allow rather than dropping below one beat per shot.
        base = max(1, total_beats // shot_count)
        allocation = [base] * shot_count
        extra = max(0, total_beats - base * shot_count)
        for index in order[:extra]:
            allocation[index] += 1
        return allocation

    remaining = total_beats - sum(allocation)
    while remaining > 0:
        progressed = False
        for index in order:
            if remaining <= 0:
                break
            current = allocation[index]
            larger = next((option for option in options if option > current), None)
            step = (larger - current) if larger is not None else 1
            if step <= remaining:
                allocation[index] += step
                remaining -= step
                progressed = True
        if not progressed:
            # What is left is smaller than any upgrade step; hand out singles.
            for index in order:
                if remaining <= 0:
                    break
                allocation[index] += 1
                remaining -= 1
            break

    return allocation


def _anchor_first_order(count: int, seed: int) -> list[int]:
    """Shot indices ordered opener, closer, then the middle shuffled."""
    if count <= 2:
        return list(range(count))
    middle = list(range(1, count - 1))
    random.Random(seed).shuffle(middle)
    return [0, count - 1, *middle]


def plan_cuts(
    grid: BeatGrid,
    preset: Preset,
    target_duration: float,
    shot_count: int,
    seed: int = 0,
) -> list[float]:
    """Cut times in absolute seconds, every one of them on a beat.

    The returned length is rounded to a whole number of beats, so the video may
    come out a fraction of a second either side of *target_duration*. That is
    deliberate: ending mid-beat is audible when the clip loops, and looping is
    where this format earns its watch time.
    """
    seconds_per_beat = grid.seconds_per_beat
    if seconds_per_beat <= 0 or shot_count <= 0:
        return [target_duration]

    total_beats = max(shot_count, int(round(target_duration / seconds_per_beat)))
    allocation = allocate_beats(total_beats, shot_count, preset, seed=seed)

    cuts: list[float] = []
    running = 0
    for beats in allocation:
        running += beats
        cuts.append(running * seconds_per_beat)
    return cuts


def build(
    selection: Sequence[ScoredAsset],
    preset: Preset,
    grid: BeatGrid,
    duration: float,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    seed: int = 0,
    hook: str = "",
    signoff: str = "",
) -> Storyboard:
    """Build a :class:`Storyboard` from a selection, a grid and a target length."""
    board = Storyboard(
        preset_key=preset.key, duration=duration, grid=grid,
        width=width, height=height, fps=fps, hook=hook, signoff=signoff,
    )
    if not selection:
        board.warnings.append("Nothing was selected, so there is nothing to render.")
        return board

    generator = random.Random(seed)
    cuts = plan_cuts(grid, preset, duration, len(selection), seed=seed)
    transition_names = list(preset.pacing.transition) or ["fade"]
    motion_styles = list(preset.motion.styles) or ["push_in"]

    transitions = _transition_lengths(cuts, preset.pacing.transition_seconds)

    previous_cut = 0.0
    for index, item in enumerate(selection):
        cut = cuts[index]
        visible = max(0.05, cut - previous_cut)
        transition_in = transitions[index - 1] if index > 0 else 0.0
        transition_out = transitions[index] if index < len(transitions) else 0.0
        length = visible + transition_in / 2.0 + transition_out / 2.0

        shot = Shot(
            asset=item.asset,
            index=index,
            start=previous_cut,
            visible=visible,
            length=length,
            motion=motion_styles[index % len(motion_styles)],
            transition_in=transition_in,
            transition_out=transition_out,
            transition_name=generator.choice(transition_names),
        )
        if shot.is_video:
            _fit_video(shot, board)
        board.shots.append(shot)
        previous_cut = cut

    board.duration = board.shots[-1].end if board.shots else 0.0
    return board


def _transition_lengths(cuts: Sequence[float], nominal: float) -> list[float]:
    """Transition length at each interior cut, shortened for very short shots.

    A 0.4s crossfade across a 0.5s shot leaves nothing of the shot itself, so
    each transition is capped at a fraction of the shorter neighbouring span.
    """
    lengths: list[float] = []
    previous = 0.0
    for index in range(len(cuts) - 1):
        before = cuts[index] - previous
        after = cuts[index + 1] - cuts[index]
        cap = min(before, after) * 0.5
        lengths.append(max(0.05, min(nominal, cap)))
        previous = cuts[index]
    return lengths


def _fit_video(shot: Shot, board: Storyboard) -> None:
    """Choose an in-point in a video source, slowing it only if it is too short.

    Stock clips usually open on a slate or a settling camera, so the in-point
    skips the first tenth of the file when there is room to do so.
    """
    available = shot.asset.duration
    needed = shot.length

    if available >= needed * 1.15:
        head = min(available * 0.10, 1.5)
        usable = available - head
        latest = head + max(0.0, usable - needed)
        # A deterministic but varied in-point keeps repeated use of the same
        # long clip from always showing the identical few seconds.
        offset = (hash(shot.asset.fingerprint or shot.asset.path) % 1000) / 1000.0
        shot.source_in = head + (latest - head) * offset
        return

    if available >= needed:
        shot.source_in = 0.0
        return

    # Too short: slow it down, but never past the point where motion stutters.
    ratio = needed / max(0.05, available)
    if ratio <= 2.0:
        shot.speed = 1.0 / ratio
        shot.source_in = 0.0
        return

    shot.speed = 0.5
    shot.source_in = 0.0
    board.warnings.append(
        f"{shot.asset.name} is {available:.1f}s but needs {needed:.1f}s; "
        "it will hold on its last frame."
    )


def verify(board: Storyboard, tolerance: float = 0.01) -> list[str]:
    """Check a storyboard's internal timing, returning any problems found.

    This is the guard that the xfade chain the renderer emits will actually
    reproduce the timeline the storyboard describes.
    """
    problems: list[str] = []
    if not board.shots:
        return ["storyboard has no shots"]

    expected_start = 0.0
    for shot in board.shots:
        if abs(shot.start - expected_start) > tolerance:
            problems.append(
                f"shot {shot.index} starts at {shot.start:.3f}, expected {expected_start:.3f}"
            )
        if shot.visible <= 0:
            problems.append(f"shot {shot.index} has non-positive visible span")
        required = shot.visible + shot.transition_in / 2.0 + shot.transition_out / 2.0
        if abs(shot.length - required) > tolerance:
            problems.append(
                f"shot {shot.index} length {shot.length:.3f} does not cover its span "
                f"plus overlaps ({required:.3f})"
            )
        expected_start = shot.end

    # Walk the xfade chain the way ffmpeg will, and confirm it lands on the
    # same total length the storyboard claims.
    accumulated = board.shots[0].length
    for shot in board.shots[1:]:
        accumulated += shot.length - shot.transition_in
    if abs(accumulated - board.duration) > tolerance:
        problems.append(
            f"xfade chain totals {accumulated:.3f}s but storyboard says {board.duration:.3f}s"
        )

    for index in range(len(board.shots) - 1):
        if abs(board.shots[index].transition_out - board.shots[index + 1].transition_in) > 1e-6:
            problems.append(f"transition mismatch between shots {index} and {index + 1}")

    return problems
