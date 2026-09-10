"""Beat allocation and the storyboard timing model."""

from __future__ import annotations

import pytest

from mbtok import audio, presets, storyboard
from tests.conftest import make_asset, make_scored

ALL_PRESETS = presets.names()


def selection(count: int, video_every: int = 3):
    """A selection of *count* shots. ``video_every=0`` gives stills only."""
    return [
        make_scored(
            make_asset(
                f"shot-{index}.jpg",
                kind="video" if video_every and index % video_every == 0 else "image",
                duration=8.0,
                folder=f"/lib/{index}",
                seed=index,
            )
        )
        for index in range(count)
    ]


def test_beats_are_fully_allocated():
    """Every beat is handed to some shot, none invented or lost."""
    preset = presets.get("clean-girl")
    for total, shots in ((16, 8), (17, 8), (24, 7), (9, 9), (5, 9), (40, 6)):
        allocation = storyboard.allocate_beats(total, shots, preset, seed=1)
        assert len(allocation) == shots
        assert all(beats >= 1 for beats in allocation)
        if total >= shots:
            assert sum(allocation) == total


def test_beat_allocation_never_starves_a_shot():
    """More shots than beats still gives every shot at least one beat."""
    allocation = storyboard.allocate_beats(4, 10, presets.get("y2k-chrome"), seed=0)
    assert len(allocation) == 10
    assert min(allocation) >= 1


def test_anchor_shots_get_the_extra_beats():
    """Spare beats go to the opener and closer first."""
    preset = presets.get("dark-academia")
    allocation = storyboard.allocate_beats(20, 6, preset, seed=0)
    middle = allocation[1:-1]
    assert allocation[0] >= max(middle)
    assert allocation[-1] >= max(middle)


@pytest.mark.parametrize("key", ALL_PRESETS)
def test_every_cut_lands_on_a_beat(key):
    """Across every mood, cuts are exact multiples of the beat."""
    preset = presets.get(key)
    grid = audio.fixed_grid(112.0, duration=60.0)
    cuts = storyboard.plan_cuts(grid, preset, 10.0, 8, seed=3)
    for cut in cuts:
        beats = cut / grid.seconds_per_beat
        assert beats == pytest.approx(round(beats), abs=1e-6)


def test_cuts_increase_monotonically():
    """No cut ever lands before the one preceding it."""
    grid = audio.fixed_grid(128.0, duration=60.0)
    cuts = storyboard.plan_cuts(grid, presets.get("y2k-chrome"), 8.0, 14, seed=5)
    assert cuts == sorted(cuts)
    assert len(set(cuts)) == len(cuts)


def test_duration_lands_near_the_request():
    """Snapping to whole beats stays within one beat of the target."""
    for bpm, target in ((104, 10.0), (84, 14.0), (128, 7.0), (96, 12.0)):
        grid = audio.fixed_grid(bpm, duration=60.0)
        preset = presets.get("clean-girl")
        shots = storyboard.suggest_shot_count(preset, grid, target)
        cuts = storyboard.plan_cuts(grid, preset, target, shots, seed=2)
        assert abs(cuts[-1] - target) <= grid.seconds_per_beat


@pytest.mark.parametrize("key", ALL_PRESETS)
def test_storyboard_timing_is_self_consistent(key):
    """Every mood produces a storyboard whose own verifier passes."""
    preset = presets.get(key)
    grid = audio.fixed_grid(110.0, duration=60.0)
    shots = storyboard.suggest_shot_count(preset, grid, 10.0)
    board = storyboard.build(selection(shots), preset, grid, 10.0, seed=4)
    assert storyboard.verify(board) == []


def test_xfade_chain_reproduces_the_timeline():
    """Simulating ffmpeg's own xfade arithmetic gives the stated duration.

    This is the property the renderer depends on: chaining shot lengths minus
    transition overlaps must equal the storyboard's total.
    """
    grid = audio.fixed_grid(104.0, duration=60.0)
    board = storyboard.build(selection(9), presets.get("clean-girl"), grid, 10.0, seed=6)
    total = board.shots[0].length
    for shot in board.shots[1:]:
        total += shot.length - shot.transition_in
    assert total == pytest.approx(board.duration, abs=1e-6)


def test_shots_are_contiguous():
    """Each shot begins exactly where the previous one ends."""
    grid = audio.fixed_grid(96.0, duration=60.0)
    board = storyboard.build(selection(8), presets.get("street-mono"), grid, 12.0, seed=7)
    for previous, current in zip(board.shots, board.shots[1:]):
        assert current.start == pytest.approx(previous.end, abs=1e-9)
    assert board.shots[0].start == 0.0


def test_transitions_agree_between_neighbours():
    """A shot's outgoing transition equals its neighbour's incoming one."""
    grid = audio.fixed_grid(120.0, duration=60.0)
    board = storyboard.build(selection(7), presets.get("tokyo-night"), grid, 8.0, seed=8)
    for previous, current in zip(board.shots, board.shots[1:]):
        assert previous.transition_out == pytest.approx(current.transition_in)
    assert board.shots[0].transition_in == 0.0
    assert board.shots[-1].transition_out == 0.0


def test_transitions_never_swallow_a_shot():
    """A transition is at most half of the shorter shot it joins."""
    grid = audio.fixed_grid(140.0, duration=60.0)
    board = storyboard.build(selection(16), presets.get("y2k-chrome"), grid, 7.0, seed=9)
    for previous, current in zip(board.shots, board.shots[1:]):
        assert previous.transition_out <= min(previous.visible, current.visible) * 0.5 + 1e-9


def test_video_in_point_skips_the_opening():
    """A long clip is seeked past its first moments."""
    grid = audio.fixed_grid(100.0, duration=60.0)
    shots = [make_scored(make_asset("long.mp4", kind="video", duration=30.0))]
    board = storyboard.build(shots, presets.get("clean-girl"), grid, 3.0, seed=1)
    assert board.shots[0].source_in > 0.0
    assert board.shots[0].speed == 1.0


def test_short_clip_is_slowed_not_stretched_past_reason():
    """Footage shorter than its slot slows down, within limits, and warns."""
    grid = audio.fixed_grid(60.0, duration=60.0)
    shots = [make_scored(make_asset("tiny.mp4", kind="video", duration=1.2))]
    board = storyboard.build(shots, presets.get("dark-academia"), grid, 6.0, seed=1)
    assert 0.5 <= board.shots[0].speed < 1.0
    assert board.warnings


def test_stills_are_never_speed_adjusted():
    """A photograph has no playback rate to change."""
    grid = audio.fixed_grid(100.0, duration=60.0)
    board = storyboard.build(selection(6, video_every=0), presets.get("clean-girl"), grid, 8.0)
    assert all(shot.speed == 1.0 and shot.source_in == 0.0 for shot in board.shots)


def test_empty_selection_warns_instead_of_failing():
    """Nothing selected produces an empty board with an explanation."""
    grid = audio.fixed_grid(100.0)
    board = storyboard.build([], presets.get("clean-girl"), grid, 10.0)
    assert board.shots == []
    assert board.warnings
    assert storyboard.verify(board) == ["storyboard has no shots"]


def test_verify_catches_a_broken_board():
    """A tampered shot length is reported rather than passed through."""
    grid = audio.fixed_grid(100.0, duration=60.0)
    board = storyboard.build(selection(5), presets.get("clean-girl"), grid, 8.0)
    board.shots[2].length += 0.7
    assert storyboard.verify(board)


def test_suggest_shot_count_tracks_pacing():
    """A fast mood asks for more shots than a slow one at equal length."""
    grid = audio.fixed_grid(120.0, duration=60.0)
    fast = storyboard.suggest_shot_count(presets.get("y2k-chrome"), grid, 10.0)
    slow = storyboard.suggest_shot_count(presets.get("dark-academia"), grid, 10.0)
    assert fast > slow


def test_storyboard_serialises():
    """A board converts to JSON-safe data with every shot present."""
    grid = audio.fixed_grid(104.0, duration=60.0)
    board = storyboard.build(selection(6), presets.get("clean-girl"), grid, 9.0, hook="hi")
    data = board.to_dict()
    assert len(data["shots"]) == 6
    assert data["hook"] == "hi"
    assert data["beat_grid"]["bpm"] == pytest.approx(104.0)
