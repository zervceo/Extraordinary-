"""Build and run the ffmpeg command that turns a storyboard into an MP4.

The whole render is a single ffmpeg invocation. Each shot becomes one input
with its own normalise-and-grade chain, the chains are joined with ``xfade``
transitions whose offsets come straight from the storyboard's timing model,
and the finished picture gets its film treatment and text in one pass over the
composite rather than per shot.

Nothing here generates imagery. Every pixel originates in a file the user
supplied; the filters only scale, move, grade and blend them.
"""

from __future__ import annotations

import logging
import math
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import ffmpeg as ff
from .audio import BeatGrid
from .crop import Focus
from .presets import Grade, Preset, Typography
from .storyboard import Shot, Storyboard
from .typeset import fit as fit_text
from .util import clamp

log = logging.getLogger("mbtok.render")

#: Below this, a focus point is treated as no evidence at all and the crop
#: falls back to centred. An evenly detailed frame scores low here, and for
#: such a frame the centre really is the right answer.
MIN_FOCUS_CONFIDENCE = 0.12

#: Stills are pre-scaled to this multiple of the output size before the Ken
#: Burns move, so panning happens in source pixels finer than output pixels
#: and the motion reads as smooth instead of stepped.
SUPERSAMPLE = 2


@dataclass
class RenderOptions:
    """Everything about the render that is not the storyboard itself."""

    output: Path
    music: Path | None = None
    music_offset: float = 0.0
    music_gain: float = 0.0
    fade_in: float = 0.35
    fade_out: float = 0.8
    crf: int = 20
    x264_preset: str = "medium"
    #: Ceiling in megabits/sec. Film grain is expensive to encode, and without
    #: a cap a grainy preset at a low CRF produces a file many times larger
    #: than anything a phone will upload. TikTok re-encodes on ingest anyway,
    #: so bitrate spent above this ceiling is simply thrown away.
    max_bitrate_mbps: float = 14.0
    audio_bitrate: str = "192k"
    font: str | None = None
    text_dir: Path | None = None
    overwrite: bool = True
    threads: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


class RenderError(RuntimeError):
    """Raised when a render cannot be built or ffmpeg refuses it."""


# --------------------------------------------------------------------------
# Filter fragments
# --------------------------------------------------------------------------

def _fmt(value: float, places: int = 4) -> str:
    """Format a float for a filter expression without exponent notation."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text if text and text not in ("-", "-0") else "0"


def _time(value: float) -> str:
    """Format a timeline value with enough precision to stay exact.

    Four decimal places is enough to look right and not enough to be right: a
    transition offset rounded up by even a hundredth of a millisecond asks
    ``xfade`` to read past the end of its input. Microsecond precision keeps
    every emitted time strictly inside the media it refers to.
    """
    return _fmt(value, places=6)


def cover_scale(
    width: int,
    height: int,
    focus: Focus | None = None,
    source_aspect: float = 0.0,
) -> str:
    """Scale-and-crop that fills *width* x *height* without letterboxing.

    With a *focus* point and the source's aspect ratio, the crop window is
    placed over the interesting part of the frame instead of its middle. The
    offset is written as a fraction of ``iw-ow``, so it stays correct whatever
    size the preceding scale produced.
    """
    chain = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
    )
    position = crop_position(focus, source_aspect, width / height if height else 0.0)
    if position is None:
        return chain + f"crop={width}:{height}"
    axis, fraction = position
    if axis == "x":
        return chain + f"crop={width}:{height}:x=(iw-ow)*{_fmt(fraction)}:y=0"
    return chain + f"crop={width}:{height}:x=0:y=(ih-oh)*{_fmt(fraction)}"


def crop_position(
    focus: Focus | None, source_aspect: float, target_aspect: float
) -> tuple[str, float] | None:
    """Where to place the crop window, as a fraction of the available slack.

    Returns the axis being cropped and a 0-1 position along it, or ``None``
    when the frame needs no crop on either axis or there is no focus worth
    acting on. A low-confidence focus is ignored rather than trusted, because
    an evenly detailed frame gives no real evidence about where to crop.
    """
    if focus is None or source_aspect <= 0 or target_aspect <= 0:
        return None
    if focus.confidence < MIN_FOCUS_CONFIDENCE or focus.is_centred:
        return None

    if source_aspect > target_aspect:
        window = target_aspect / source_aspect
        slack = 1.0 - window
        if slack <= 1e-6:
            return None
        return ("x", clamp((focus.x - window / 2.0) / slack, 0.0, 1.0))

    if source_aspect < target_aspect:
        window = source_aspect / target_aspect
        slack = 1.0 - window
        if slack <= 1e-6:
            return None
        return ("y", clamp((focus.y - window / 2.0) / slack, 0.0, 1.0))

    return None


def kenburns_expressions(motion: str, zoom: float, frames: int) -> tuple[str, str, str]:
    """Return the ``z``, ``x`` and ``y`` expressions for a Ken Burns move.

    ``on`` is zoompan's output frame counter, so ``on/(frames-1)`` is progress
    through the shot in 0-1. Positions are expressed against ``iw``/``ih`` and
    the current ``zoom``, which zoompan evaluates before ``x`` and ``y``.
    """
    span = max(1, frames - 1)
    progress = f"(on/{span})"
    amount = max(0.0, zoom)
    held = _fmt(1.0 + amount)
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if motion == "push_in":
        return (f"1+{_fmt(amount)}*{progress}", centre_x, centre_y)
    if motion == "pull_out":
        return (f"{held}-{_fmt(amount)}*{progress}", centre_x, centre_y)
    if motion == "pan_right":
        return (held, f"(iw-iw/zoom)*{progress}", centre_y)
    if motion == "pan_left":
        return (held, f"(iw-iw/zoom)*(1-{progress})", centre_y)
    if motion == "drift_up":
        return (held, centre_x, f"(ih-ih/zoom)*(1-{progress})")
    if motion == "drift_down":
        return (held, centre_x, f"(ih-ih/zoom)*{progress}")
    if motion == "still":
        return ("1", centre_x, centre_y)
    # An unknown style should still produce a shot rather than fail the render.
    return (f"1+{_fmt(amount)}*{progress}", centre_x, centre_y)


def grade_filters(grade: Grade) -> list[str]:
    """The ``eq`` and ``colorbalance`` filters implementing a preset's grade."""
    filters: list[str] = []
    if (
        grade.contrast != 1.0
        or grade.brightness != 0.0
        or grade.saturation != 1.0
        or grade.gamma != 1.0
    ):
        filters.append(
            "eq="
            f"contrast={_fmt(grade.contrast)}:"
            f"brightness={_fmt(grade.brightness)}:"
            f"saturation={_fmt(grade.saturation)}:"
            f"gamma={_fmt(grade.gamma)}"
        )
    if any(grade.shadows) or any(grade.midtones) or any(grade.highlights):
        shadow_r, shadow_g, shadow_b = grade.shadows
        mid_r, mid_g, mid_b = grade.midtones
        high_r, high_g, high_b = grade.highlights
        filters.append(
            "colorbalance="
            f"rs={_fmt(shadow_r)}:gs={_fmt(shadow_g)}:bs={_fmt(shadow_b)}:"
            f"rm={_fmt(mid_r)}:gm={_fmt(mid_g)}:bm={_fmt(mid_b)}:"
            f"rh={_fmt(high_r)}:gh={_fmt(high_g)}:bh={_fmt(high_b)}"
        )
    return filters


def shot_chain(shot: Shot, board: Storyboard, preset: Preset) -> str:
    """The full filter chain for one shot, from decoded input to xfade input."""
    width, height = board.width, board.height
    fps = board.fps
    parts: list[str] = []

    focus = shot.asset.focus
    aspect = shot.asset.aspect

    if shot.is_video:
        if shot.speed != 1.0 and shot.speed > 0:
            parts.append(f"setpts=PTS/{_fmt(shot.speed)}")
        parts.append(cover_scale(width, height, focus, aspect))
        parts.append(f"fps={fps}")
        # Clone the final frame rather than come up short: a clip a few frames
        # shy of its slot would otherwise shorten the whole timeline.
        parts.append(f"tpad=stop_mode=clone:stop_duration={_time(shot.length + 0.5)}")
        parts.append(f"trim=duration={_time(shot.length)}")
        parts.append("setpts=PTS-STARTPTS")
    else:
        frames = max(2, int(round(shot.length * fps)))
        zoom_expression, x_expression, y_expression = kenburns_expressions(
            shot.motion, preset.motion.zoom, frames
        )
        # Exactly one source frame, whatever the container claims to hold.
        parts.append("trim=start_frame=0:end_frame=1")
        parts.append("setpts=PTS-STARTPTS")
        parts.append(cover_scale(width * SUPERSAMPLE, height * SUPERSAMPLE, focus, aspect))
        parts.append(
            f"zoompan=z='{zoom_expression}':x='{x_expression}':y='{y_expression}':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        )

    parts.extend(grade_filters(preset.grade))
    parts.append("setsar=1")
    parts.append("format=yuv420p")
    parts.append("settb=AVTB")
    return ",".join(parts)


def finish_chain(preset: Preset, board: Storyboard) -> list[str]:
    """Film treatment applied once to the composite, after all the cuts.

    Grain and vignette belong here rather than on each shot: crossfading two
    independently grained shots halves the grain through every transition, and
    a vignette that fades in and out at each cut reads as a mistake.
    """
    grade = preset.grade
    filters: list[str] = []
    if grade.vignette > 0:
        angle = math.pi / 5.0 + clamp(grade.vignette, 0.0, 1.0) * (math.pi / 9.0)
        filters.append(f"vignette=angle={_fmt(angle, 5)}:mode=forward")
    if grade.grain > 0:
        strength = int(round(clamp(grade.grain, 0.0, 60.0)))
        filters.append(f"noise=alls={strength}:allf=t+u")
    return filters


def bloom_graph(source: str, target: str, amount: float) -> str:
    """A screen-blended soft glow, which is what sells "shot on film" light."""
    strength = clamp(amount, 0.0, 1.0)
    return (
        f"[{source}]split[{source}_a][{source}_b];"
        f"[{source}_b]gblur=sigma=24[{source}_glow];"
        f"[{source}_a][{source}_glow]blend=all_mode=screen:"
        f"all_opacity={_fmt(strength)}[{target}]"
    )


# --------------------------------------------------------------------------
# Text overlays
# --------------------------------------------------------------------------

def apply_case(text: str, case: str) -> str:
    """Apply a typography case rule to *text*."""
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return " ".join(word[:1].upper() + word[1:] for word in text.split(" "))
    return text


def escape_filter_path(path: str) -> str:
    """Escape a path for use inside a filtergraph option value."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


@dataclass
class TextOverlay:
    """One piece of on-screen text and when it appears."""

    text: str
    start: float
    end: float
    style: Typography
    #: Font size in pixels, already reduced if the text needed to shrink to fit.
    size: int = 48
    fade: float = 0.35
    file: Path | None = None

    def drawtext(self, board: Storyboard, font: str, align: bool = False) -> str:
        """Render this overlay as a ``drawtext`` filter.

        The text is passed via ``textfile`` rather than inline. Hook lines
        contain apostrophes, colons and commas, all of which are filtergraph
        metacharacters; a file sidesteps three layers of escaping entirely.
        """
        if self.file is None:
            raise RenderError("text overlay was not written to a file before rendering")

        style = self.style
        size = max(18, self.size)
        # Text sits over photography, not a flat card, so it needs a real
        # shadow to stay readable over a bright frame.
        shadow_offset = max(2, size // 22)
        # Upper and centre text grows downward from a fixed point; lower text
        # is anchored by its bottom edge so a second line pushes it up, away
        # from the caption and buttons rather than into them.
        y_position = {
            "upper": "h*0.17",
            "center": "(h-text_h)/2",
            "lower": "h*0.80-text_h",
        }.get(style.position, "h*0.17")

        fade = max(0.05, min(self.fade, (self.end - self.start) / 2.2))
        alpha = (
            f"if(lt(t,{_time(self.start)}),0,"
            f"if(lt(t,{_time(self.start + fade)}),(t-{_time(self.start)})/{_time(fade)},"
            f"if(lt(t,{_time(self.end - fade)}),1,"
            f"if(lt(t,{_time(self.end)}),({_time(self.end)}-t)/{_time(fade)},0))))"
        )

        options = [
            f"fontfile='{escape_filter_path(font)}'",
            f"textfile='{escape_filter_path(str(self.file))}'",
            f"fontsize={size}",
            f"fontcolor={_color(style.color)}",
            "x=(w-text_w)/2",
            f"y={y_position}",
            f"alpha='{alpha}'",
            f"enable='between(t,{_time(self.start)},{_time(self.end)})'",
            "line_spacing=12",
            f"shadowcolor={_color(style.shadow)}",
            "shadowx=0",
            f"shadowy={shadow_offset}",
        ]
        # Without text_align, drawtext left-aligns the lines inside a
        # multi-line block, which reads as a mistake under a centred hook.
        if align and "\n" in self.text:
            options.append("text_align=C")
        if style.box:
            options.append("box=1")
            options.append(f"boxcolor={_color(style.box_color)}")
            options.append("boxborderw=28")
        return "drawtext=" + ":".join(options)


def _color(value: str) -> str:
    """Convert ``#rrggbb`` or ``#rrggbbaa`` into ffmpeg's colour syntax."""
    text = value.strip()
    if not text.startswith("#"):
        return text
    body = text[1:]
    if len(body) == 8:
        return f"0x{body[:6]}@{int(body[6:], 16) / 255.0:.3f}"
    return f"0x{body}"


def build_overlays(
    board: Storyboard,
    preset: Preset,
    options: RenderOptions,
) -> list[TextOverlay]:
    """Create the hook and sign-off overlays and write their text files.

    The hook lands almost immediately. The first second decides whether the
    video is watched at all, and a mood board with no stated idea gives a
    viewer nothing to stay for.
    """
    overlays: list[TextOverlay] = []
    directory = options.text_dir or options.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    style = preset.typography

    if board.hook:
        fitted = fit_text(
            apply_case(board.hook, style.case),
            board.width * style.size_ratio,
            board.width,
            max_lines=3,
            tracking=style.letter_spacing,
        )
        path = directory / f"{options.output.stem}.hook.txt"
        path.write_text(fitted.text, encoding="utf-8")
        hold = clamp(board.duration * 0.32, 1.6, 3.4)
        overlays.append(
            TextOverlay(
                text=fitted.text, start=0.25, end=0.25 + hold,
                style=style, size=fitted.size, file=path,
            )
        )

    if board.signoff and board.duration > 5.0:
        signoff_style = Typography(
            fonts=style.fonts,
            case=style.case,
            size_ratio=style.size_ratio * 0.72,
            color=style.color,
            shadow=style.shadow,
            letter_spacing=style.letter_spacing,
            position="lower" if style.position != "lower" else "center",
            box=False,
        )
        fitted = fit_text(
            apply_case(board.signoff, signoff_style.case),
            board.width * signoff_style.size_ratio,
            board.width,
            max_lines=2,
            tracking=signoff_style.letter_spacing,
        )
        path = directory / f"{options.output.stem}.signoff.txt"
        path.write_text(fitted.text, encoding="utf-8")
        overlays.append(
            TextOverlay(
                text=fitted.text,
                start=max(0.0, board.duration - 2.6),
                end=board.duration,
                style=signoff_style,
                size=fitted.size,
                file=path,
            )
        )

    return overlays


# --------------------------------------------------------------------------
# Command assembly
# --------------------------------------------------------------------------

@dataclass
class RenderPlan:
    """A fully built ffmpeg invocation, inspectable before it is run."""

    command: list[str]
    filter_complex: str
    output: Path
    duration: float
    overlays: list[TextOverlay] = field(default_factory=list)

    @property
    def shell(self) -> str:
        """The command as a copy-pasteable shell string."""
        return " ".join(shlex.quote(part) for part in self.command)


def build_command(
    board: Storyboard,
    preset: Preset,
    options: RenderOptions,
    binaries: ff.Binaries,
) -> RenderPlan:
    """Assemble the complete ffmpeg command for *board*."""
    if not board.shots:
        raise RenderError("storyboard has no shots to render")

    font = options.font or ff.find_font(preset.typography.fonts)
    overlays = build_overlays(board, preset, options) if font else []
    if not font and (board.hook or board.signoff):
        log.warning("no usable font found; rendering without text overlays")

    command: list[str] = [binaries.ffmpeg, "-hide_banner", "-nostdin"]
    command.append("-y" if options.overwrite else "-n")

    for shot in board.shots:
        command.extend(_input_args(shot))

    music_index = len(board.shots)
    if options.music:
        command.extend([
            "-stream_loop", "-1",
            "-ss", _time(max(0.0, options.music_offset)),
            "-i", str(options.music),
        ])

    graph, video_label = _video_graph(
        board, preset, overlays, font, align=ff.supports_drawtext_align(binaries)
    )
    audio_label = None
    if options.music:
        graph += ";" + _audio_graph(music_index, board.duration, options)
        audio_label = "aout"

    command.extend(["-filter_complex", graph])
    command.extend(["-map", f"[{video_label}]"])
    if audio_label:
        command.extend(["-map", f"[{audio_label}]"])

    command.extend([
        "-c:v", "libx264",
        "-preset", options.x264_preset,
        "-crf", str(options.crf),
        "-maxrate", f"{options.max_bitrate_mbps:g}M",
        "-bufsize", f"{options.max_bitrate_mbps * 1.6:g}M",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.1",
        "-r", str(board.fps),
        "-g", str(board.fps * 2),
        "-movflags", "+faststart",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
    ])
    if audio_label:
        command.extend(["-c:a", "aac", "-b:a", options.audio_bitrate, "-ar", "48000", "-ac", "2"])
    if options.threads:
        command.extend(["-threads", str(options.threads)])

    command.extend(["-map_metadata", "-1"])
    for key, value in options.metadata.items():
        command.extend(["-metadata", f"{key}={value}"])

    command.extend(["-t", _time(board.duration)])
    command.append(str(options.output))

    return RenderPlan(
        command=command,
        filter_complex=graph,
        output=options.output,
        duration=board.duration,
        overlays=overlays,
    )


def _input_args(shot: Shot) -> list[str]:
    """Input-side arguments for one shot.

    Stills are decoded as a single frame and animated by zoompan, so they take
    no duration here. Footage is seeked and length-limited on the input side,
    which is both faster and frame-accurate.
    """
    if shot.is_video:
        consumed = shot.length * (shot.speed if shot.speed > 0 else 1.0)
        args = []
        if shot.source_in > 0:
            args += ["-ss", _time(shot.source_in)]
        args += ["-t", _time(consumed + 0.25), "-i", shot.asset.path]
        return args
    return ["-i", shot.asset.path]


def _video_graph(
    board: Storyboard,
    preset: Preset,
    overlays: Sequence[TextOverlay],
    font: str | None,
    align: bool = False,
) -> tuple[str, str]:
    """Build the video half of the filtergraph and return it with its label."""
    segments: list[str] = []

    for index, shot in enumerate(board.shots):
        segments.append(f"[{index}:v]{shot_chain(shot, board, preset)}[s{index}]")

    current = "s0"
    accumulated = board.shots[0].length
    for index in range(1, len(board.shots)):
        shot = board.shots[index]
        transition = shot.transition_in
        # Centre the transition on the cut: it starts half a transition before
        # the beat and ends half a transition after it.
        offset = max(0.0, accumulated - transition)
        label = f"x{index}"
        segments.append(
            f"[{current}][s{index}]xfade="
            f"transition={shot.transition_name}:"
            f"duration={_time(max(0.02, transition))}:"
            f"offset={_time(offset)}[{label}]"
        )
        accumulated += shot.length - transition
        current = label

    if preset.grade.blur_bloom > 0:
        segments.append(bloom_graph(current, f"{current}_bloom", preset.grade.blur_bloom))
        current = f"{current}_bloom"

    finish = finish_chain(preset, board)
    if font:
        finish.extend(overlay.drawtext(board, font, align=align) for overlay in overlays)
    finish.append("format=yuv420p")
    segments.append(f"[{current}]{','.join(finish)}[vout]")

    return (";".join(segments), "vout")


def _audio_graph(index: int, duration: float, options: RenderOptions) -> str:
    """Build the audio half of the filtergraph."""
    fade_out_start = max(0.0, duration - options.fade_out)
    parts = [
        f"atrim=duration={_time(duration)}",
        "asetpts=PTS-STARTPTS",
        f"afade=t=in:st=0:d={_time(options.fade_in)}",
        f"afade=t=out:st={_time(fade_out_start)}:d={_time(options.fade_out)}",
    ]
    if options.music_gain:
        parts.append(f"volume={_fmt(options.music_gain)}dB")
    parts.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    return f"[{index}:a]{','.join(parts)}[aout]"


def render(
    board: Storyboard,
    preset: Preset,
    options: RenderOptions,
    binaries: ff.Binaries | None = None,
    dry_run: bool = False,
    timeout: float = 1800.0,
) -> RenderPlan:
    """Build and (unless *dry_run*) execute the render."""
    binaries = binaries or ff.find_binaries()
    plan = build_command(board, preset, options, binaries)
    if dry_run:
        return plan

    options.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        ff.run(plan.command, timeout=timeout)
    except ff.FFmpegError as exc:
        raise RenderError(f"render failed for {options.output.name}:\n{exc}") from exc
    return plan


def music_start_for(grid: BeatGrid) -> float:
    """Where to start the music so video time zero lands on a downbeat.

    Every cut in the storyboard sits at a whole number of beats from zero, so
    starting the track on its first strong beat is what makes those cuts land.
    """
    return max(0.0, grid.offset)


# --------------------------------------------------------------------------
# Contact sheets
# --------------------------------------------------------------------------

def contact_sheet_command(
    board: Storyboard,
    preset: Preset,
    output: Path,
    binaries: ff.Binaries,
    thumb_width: int = 270,
    columns: int = 0,
    font: str | None = None,
) -> list[str]:
    """Build an ffmpeg command that tiles one frame per shot into a grid.

    A full render takes the better part of a minute; deciding whether a board
    is worth rendering takes one look. Every thumbnail goes through the same
    crop and grade the video would use, so what the sheet shows is what the
    video will contain.
    """
    if not board.shots:
        raise RenderError("storyboard has no shots to preview")

    count = len(board.shots)
    grid_columns = columns or min(5, count)
    grid_rows = math.ceil(count / grid_columns)
    thumb_height = int(round(thumb_width * board.height / board.width))

    command = [binaries.ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for shot in board.shots:
        if shot.is_video:
            seek = shot.source_in + shot.visible / 2.0
            command += ["-ss", _time(max(0.0, seek)), "-i", shot.asset.path]
        else:
            command += ["-i", shot.asset.path]

    segments: list[str] = []
    for index, shot in enumerate(board.shots):
        parts = [
            "trim=start_frame=0:end_frame=1",
            "setpts=PTS-STARTPTS",
            cover_scale(thumb_width, thumb_height, shot.asset.focus, shot.asset.aspect),
        ]
        parts.extend(grade_filters(preset.grade))
        if font:
            parts.append(
                "drawtext="
                + ":".join(
                    [
                        f"fontfile='{escape_filter_path(font)}'",
                        f"text='{index + 1}'",
                        f"fontsize={max(14, thumb_width // 12)}",
                        "fontcolor=white",
                        "box=1",
                        "boxcolor=0x000000@0.55",
                        "boxborderw=8",
                        "x=12",
                        "y=12",
                    ]
                )
            )
        parts.append("setsar=1")
        parts.append("format=rgb24")
        segments.append(f"[{index}:v]{','.join(parts)}[t{index}]")

    # Pad the last row so tile always receives a full grid.
    blanks = grid_columns * grid_rows - count
    labels = [f"[t{index}]" for index in range(count)]
    if blanks:
        segments.append(
            f"color=c=0x101010:s={thumb_width}x{thumb_height}:d=1,"
            f"trim=end_frame=1,setsar=1,format=rgb24[blank]"
        )
        if blanks > 1:
            segments.append(
                "[blank]split=" + str(blanks)
                + "".join(f"[b{index}]" for index in range(blanks))
            )
            labels.extend(f"[b{index}]" for index in range(blanks))
        else:
            labels.append("[blank]")

    segments.append(
        "".join(labels)
        + f"concat=n={len(labels)}:v=1:a=0,"
        + f"tile={grid_columns}x{grid_rows}:padding=6:margin=10:color=0x101010[sheet]"
    )

    command += [
        "-filter_complex", ";".join(segments),
        "-map", "[sheet]",
        "-frames:v", "1",
        str(output),
    ]
    return command


def contact_sheet(
    board: Storyboard,
    preset: Preset,
    output: Path,
    binaries: ff.Binaries | None = None,
    thumb_width: int = 270,
    columns: int = 0,
    dry_run: bool = False,
    timeout: float = 300.0,
) -> Path:
    """Render a contact sheet of the board and return its path."""
    binaries = binaries or ff.find_binaries()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = contact_sheet_command(
        board, preset, output, binaries,
        thumb_width=thumb_width, columns=columns,
        font=ff.find_font(preset.typography.fonts),
    )
    if not dry_run:
        try:
            ff.run(command, timeout=timeout)
        except ff.FFmpegError as exc:
            raise RenderError(f"contact sheet failed for {output.name}:\n{exc}") from exc
    return output

