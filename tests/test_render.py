"""Filter graph construction. These tests never invoke ffmpeg."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mbtok import audio, ffmpeg as ff, presets, render, storyboard
from tests.conftest import make_asset, make_scored

BINARIES = ff.Binaries(ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe")
ALL_PRESETS = presets.names()


def build_board(key: str = "clean-girl", shots: int = 7, duration: float = 10.0, **kwargs):
    """A storyboard ready to hand to the renderer."""
    preset = presets.get(key)
    grid = audio.fixed_grid(104.0, offset=0.3, duration=60.0)
    selection = [
        make_scored(
            make_asset(
                f"shot-{index}.jpg",
                kind="video" if index % 3 == 0 else "image",
                duration=9.0,
                folder=f"/lib/{index}",
                seed=index,
            )
        )
        for index in range(shots)
    ]
    return preset, storyboard.build(selection, preset, grid, duration, seed=11, **kwargs)


def plan_for(tmp_path: Path, key: str = "clean-girl", **kwargs):
    """Build a render plan into *tmp_path*."""
    preset, board = build_board(key, **kwargs)
    options = render.RenderOptions(
        output=tmp_path / "post.mp4",
        font="/fake/font.ttf",
        text_dir=tmp_path,
    )
    return preset, board, render.build_command(board, preset, options, BINARIES)


def test_one_input_per_shot(tmp_path):
    """Each shot contributes exactly one ffmpeg input."""
    _, board, plan = plan_for(tmp_path)
    assert plan.command.count("-i") == len(board.shots)


def test_stills_take_no_input_duration(tmp_path):
    """A still is decoded as one frame, not looped for a length."""
    _, board, plan = plan_for(tmp_path)
    still = next(shot for shot in board.shots if not shot.is_video)
    args = render._input_args(still)
    assert args == ["-i", still.asset.path]


def test_video_inputs_are_seeked_and_limited(tmp_path):
    """Footage is trimmed on the input side, which is faster and exact."""
    _, board, _ = plan_for(tmp_path)
    clip = next(shot for shot in board.shots if shot.is_video)
    args = render._input_args(clip)
    assert "-t" in args
    if clip.source_in > 0:
        assert args[0] == "-ss"


def test_xfade_offsets_centre_on_the_cut(tmp_path):
    """Each transition starts half its length before the beat it lands on."""
    _, board, plan = plan_for(tmp_path)
    offsets = [
        float(match.group(2))
        for match in re.finditer(
            r"xfade=transition=\w+:duration=([\d.]+):offset=([\d.]+)", plan.filter_complex
        )
    ]
    expected = []
    accumulated = board.shots[0].length
    for shot in board.shots[1:]:
        expected.append(accumulated - shot.transition_in)
        accumulated += shot.length - shot.transition_in
    assert offsets == pytest.approx(expected, abs=1e-3)


def test_xfade_offsets_increase(tmp_path):
    """Transitions are laid out in order along the timeline."""
    _, _, plan = plan_for(tmp_path, shots=9)
    offsets = [float(m.group(1)) for m in re.finditer(r"offset=([\d.]+)", plan.filter_complex)]
    assert offsets == sorted(offsets)


def test_transition_never_runs_past_its_input(tmp_path):
    """A transition always ends within the footage available to it."""
    _, board, plan = plan_for(tmp_path)
    accumulated = board.shots[0].length
    for match, shot in zip(
        re.finditer(r"duration=([\d.]+):offset=([\d.]+)", plan.filter_complex),
        board.shots[1:],
    ):
        duration, offset = float(match.group(1)), float(match.group(2))
        assert offset + duration <= accumulated + 1e-6
        accumulated += shot.length - shot.transition_in


def test_single_shot_needs_no_transition(tmp_path):
    """A one-shot board produces a graph with no xfade at all."""
    _, _, plan = plan_for(tmp_path, shots=1, duration=4.0)
    assert "xfade" not in plan.filter_complex


def test_graph_labels_are_unique_and_connected(tmp_path):
    """Every produced label is consumed, and none is produced twice."""
    _, _, plan = plan_for(tmp_path, shots=8)
    produced = re.findall(r"\[([a-z0-9_]+)\](?=;|$)", plan.filter_complex)
    assert len(produced) == len(set(produced))
    consumed = set(re.findall(r"\[([a-z0-9_]+)\](?=\[|[a-z])", plan.filter_complex))
    for label in produced:
        assert label == "vout" or label in consumed


def test_kenburns_expressions_stay_in_range():
    """Every move produces expressions that are valid at both ends of a shot."""
    for motion in ("push_in", "pull_out", "pan_left", "pan_right", "drift_up",
                   "drift_down", "still", "unknown-style"):
        zoom_expr, x_expr, y_expr = render.kenburns_expressions(motion, 0.08, 90)
        for expression in (zoom_expr, x_expr, y_expr):
            assert expression
            assert "nan" not in expression.lower()
        for progress in (0.0, 1.0):
            value = eval(  # noqa: S307 - evaluating our own generated expression
                zoom_expr.replace("on", str(progress * 89)),
                {"__builtins__": {}},
                {},
            )
            assert 0.95 <= value <= 1.0 + 0.08 + 1e-9


def test_kenburns_single_frame_shot_does_not_divide_by_zero():
    """A one-frame shot still yields a usable expression."""
    zoom_expr, _, _ = render.kenburns_expressions("push_in", 0.08, 1)
    assert "/0" not in zoom_expr


@pytest.mark.parametrize("key", ALL_PRESETS)
def test_every_preset_builds_a_graph(tmp_path, key):
    """No mood produces a malformed or empty filtergraph."""
    _, board, plan = plan_for(tmp_path, key=key)
    assert plan.filter_complex
    assert plan.filter_complex.count("[vout]") == 1
    assert ";;" not in plan.filter_complex
    assert ",," not in plan.filter_complex
    for index in range(len(board.shots)):
        assert f"[{index}:v]" in plan.filter_complex


def test_grade_filters_reflect_the_preset():
    """A preset with a grade emits eq and colorbalance; a neutral one does not."""
    graded = render.grade_filters(presets.get("tokyo-night").grade)
    assert any(part.startswith("eq=") for part in graded)
    assert any(part.startswith("colorbalance=") for part in graded)
    assert render.grade_filters(presets.Grade()) == []


def test_grain_and_vignette_apply_once_after_the_cuts(tmp_path):
    """Film treatment sits on the composite, not on each shot."""
    _, _, plan = plan_for(tmp_path, key="dark-academia")
    assert plan.filter_complex.count("noise=alls") == 1
    assert plan.filter_complex.count("vignette=") == 1
    tail = plan.filter_complex.split(";")[-1]
    assert "noise=alls" in tail and "vignette=" in tail


def test_bloom_only_when_the_preset_asks(tmp_path):
    """Only presets with a bloom amount pay for the blur pass."""
    _, _, with_bloom = plan_for(tmp_path, key="tokyo-night")
    _, _, without = plan_for(tmp_path, key="street-mono")
    assert "gblur" in with_bloom.filter_complex
    assert "gblur" not in without.filter_complex


def test_output_is_vertical_and_web_ready(tmp_path):
    """Encoder settings match what a phone-first platform expects."""
    _, _, plan = plan_for(tmp_path)
    command = plan.command
    assert "yuv420p" in command
    assert "+faststart" in command
    assert "libx264" in command
    assert "-maxrate" in command
    assert "1080x1920" in plan.filter_complex


def test_metadata_is_stripped_then_replaced(tmp_path):
    """Source metadata is dropped and our own provenance note added."""
    preset, board = build_board()
    options = render.RenderOptions(
        output=tmp_path / "post.mp4",
        text_dir=tmp_path,
        metadata={"comment": "no generative AI"},
    )
    plan = render.build_command(board, preset, options, BINARIES)
    assert "-map_metadata" in plan.command
    assert plan.command[plan.command.index("-map_metadata") + 1] == "-1"
    assert "comment=no generative AI" in plan.command


def test_audio_graph_is_absent_without_music(tmp_path):
    """A silent render maps no audio stream."""
    _, _, plan = plan_for(tmp_path)
    assert "[aout]" not in plan.filter_complex
    assert "-c:a" not in plan.command


def test_audio_graph_trims_and_fades(tmp_path):
    """With music, the track is trimmed to length and faded at both ends."""
    preset, board = build_board()
    options = render.RenderOptions(
        output=tmp_path / "post.mp4", music=Path("/music/track.mp3"),
        music_offset=0.31, text_dir=tmp_path,
    )
    plan = render.build_command(board, preset, options, BINARIES)
    assert "[aout]" in plan.filter_complex
    assert "afade=t=in" in plan.filter_complex
    assert "afade=t=out" in plan.filter_complex
    assert "-stream_loop" in plan.command
    assert "0.31" in plan.command


def test_music_starts_on_the_first_downbeat():
    """Video time zero lines up with the track's first strong beat."""
    grid = audio.fixed_grid(120.0, offset=0.47)
    assert render.music_start_for(grid) == pytest.approx(0.47)


def test_text_is_passed_by_file_not_inline(tmp_path):
    """Hook text goes through textfile, avoiding filtergraph escaping."""
    _, _, plan = plan_for(tmp_path, hook="it's 5:07, rain, commas, [brackets]")
    assert "textfile=" in plan.filter_complex
    assert "it's 5:07" not in plan.filter_complex
    written = (tmp_path / "post.hook.txt").read_text(encoding="utf-8")
    assert "5:07" in written


def test_long_hooks_are_wrapped_not_clipped(tmp_path):
    """A hook too wide for the frame is wrapped onto more lines."""
    long_hook = "this is a genuinely very long hook line that cannot possibly fit"
    _, _, plan = plan_for(tmp_path, key="clean-girl", hook=long_hook)
    written = (tmp_path / "post.hook.txt").read_text(encoding="utf-8")
    assert "\n" in written
    assert plan.overlays[0].size <= round(1080 * presets.get("clean-girl").typography.size_ratio)


def test_no_text_means_no_drawtext(tmp_path):
    """A board without a hook or sign-off renders no text filters."""
    _, _, plan = plan_for(tmp_path, hook="", signoff="")
    assert "drawtext" not in plan.filter_complex


def test_signoff_appears_at_the_end(tmp_path):
    """The sign-off overlay is scheduled against the end of the video."""
    _, board, plan = plan_for(tmp_path, hook="hello", signoff="@handle")
    signoff = plan.overlays[-1]
    assert signoff.end == pytest.approx(board.duration)
    assert signoff.start < board.duration


def test_case_rules():
    """Typography case conversion covers each supported rule."""
    assert render.apply_case("mixed Case here", "upper") == "MIXED CASE HERE"
    assert render.apply_case("MIXED Case", "lower") == "mixed case"
    assert render.apply_case("mixed case here", "title") == "Mixed Case Here"
    assert render.apply_case("Leave It", "as_is") == "Leave It"


def test_colour_conversion_handles_alpha():
    """Eight-digit hex becomes an ffmpeg colour with an alpha suffix."""
    assert render._color("#ffffff") == "0xffffff"
    assert render._color("#00000080").startswith("0x000000@")
    assert render._color("white") == "white"


def test_filter_paths_are_escaped():
    """Colons in a font path cannot break out of the option value."""
    assert render.escape_filter_path("/a/b:c/font.ttf") == "/a/b\\:c/font.ttf"


def test_empty_storyboard_is_refused(tmp_path):
    """Rendering nothing raises rather than producing a broken command."""
    preset = presets.get("clean-girl")
    board = storyboard.Storyboard(preset_key=preset.key)
    with pytest.raises(render.RenderError):
        render.build_command(
            board, preset, render.RenderOptions(output=tmp_path / "x.mp4"), BINARIES
        )


def test_shell_string_is_quoted(tmp_path):
    """The printable command escapes the filtergraph so it can be pasted."""
    _, _, plan = plan_for(tmp_path)
    assert "filter_complex" in plan.shell
    assert plan.shell.count("'") >= 2
