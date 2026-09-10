"""Orchestration: library plus preset plus music becomes a finished post.

The CLI is a thin shell over this module. Keeping the sequencing here means the
interesting decisions - which assets, how long, which track - can be tested
without going through argument parsing.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import audio as audio_mod
from . import captions as captions_mod
from . import curate, ffmpeg as ff, presets, render, report, storyboard
from .config import Config
from .ledger import Ledger, Post
from .library import Library
from .presets import Preset
from .util import expand, slugify, state_dir

log = logging.getLogger("mbtok.pipeline")


class PipelineError(RuntimeError):
    """Raised when a post cannot be built from the available material."""


@dataclass
class MakeRequest:
    """One video to produce."""

    preset_key: str
    duration: float = 10.0
    music: Path | None = None
    bpm: float | None = None
    slug: str = ""
    hook: str | None = None
    shots: int | None = None
    seed: int | None = None
    no_text: bool = False
    dry_run: bool = False


@dataclass
class MakeResult:
    """What a single ``make`` produced."""

    bundle: report.PostBundle
    plan: render.RenderPlan
    sidecars: dict[str, Path] = field(default_factory=dict)
    skipped: bool = False


def pick_music(config: Config, explicit: Path | None, seed: int) -> Path | None:
    """Choose a track: the one given, or one from the configured music folder."""
    if explicit:
        path = expand(explicit)
        if not path.is_file():
            raise PipelineError(f"music file not found: {path}")
        return path
    if not config.music_dir:
        return None
    folder = expand(config.music_dir)
    if not folder.is_dir():
        log.warning("music_dir %s does not exist", folder)
        return None
    tracks = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and ff.kind_for(path) == "audio"
    )
    if not tracks:
        log.warning("no audio files in %s", folder)
        return None
    return random.Random(seed).choice(tracks)


def beat_grid(
    music: Path | None,
    bpm: float | None,
    binaries: ff.Binaries,
    duration: float,
) -> audio_mod.BeatGrid:
    """Get a beat grid from the track, from an explicit BPM, or a default."""
    if music is None:
        return audio_mod.fixed_grid(bpm or 100.0, duration=duration)
    grid = audio_mod.analyze_track(music, binaries, bpm=bpm)
    if grid.source == "estimated" and grid.confidence < 0.15:
        log.warning(
            "weak beat detection on %s (confidence %.2f); pass --bpm for exact cuts",
            music.name, grid.confidence,
        )
    return grid


def make(
    library: Library,
    config: Config,
    request: MakeRequest,
    project_root: Path | str = ".",
    ledger: Ledger | None = None,
    binaries: ff.Binaries | None = None,
) -> MakeResult:
    """Build and render one post."""
    binaries = binaries or ff.find_binaries()
    preset = presets.get(request.preset_key)
    ledger = Ledger.load(project_root) if ledger is None else ledger

    slug = request.slug or default_slug(preset)
    seed = request.seed if request.seed is not None else _seed_from(slug)

    music = pick_music(config, request.music, seed)
    grid = beat_grid(music, request.bpm, binaries, request.duration)

    shot_count = request.shots or storyboard.suggest_shot_count(preset, grid, request.duration)
    cooling = ledger.cooling(config.cooldown_days)

    selection, selection_report = curate.select(
        library.usable,
        preset,
        shot_count,
        exclude=sorted(cooling),
        min_quality=config.min_quality,
        diversity=config.diversity,
    )
    if not selection:
        retry_note = (
            " Every candidate is inside its cooldown window; try --cooldown 0."
            if cooling
            else ""
        )
        raise PipelineError(
            f"no assets matched the '{preset.key}' mood.{retry_note} "
            + " ".join(selection_report.warnings)
        )

    selection = curate.order_shots(selection)
    copy = captions_mod.write(preset, slug, used_hooks=_recent_hooks(ledger, preset))
    hook = "" if request.no_text else (request.hook if request.hook is not None else copy.hook)
    signoff = "" if request.no_text else (config.handle or copy.signoff)

    board = storyboard.build(
        selection, preset, grid, request.duration,
        fps=config.fps, width=config.width, height=config.height,
        seed=seed, hook=hook, signoff=signoff,
    )
    problems = storyboard.verify(board)
    if problems:
        raise PipelineError("storyboard timing is inconsistent: " + "; ".join(problems))

    output_dir = config.resolved_output(project_root)
    output = output_dir / f"{slug}.mp4"
    options = render.RenderOptions(
        output=output,
        music=music,
        music_offset=render.music_start_for(grid) if music else 0.0,
        crf=config.crf,
        max_bitrate_mbps=config.max_bitrate_mbps,
        font=config.font or None,
        # drawtext reads its strings from files. They are build intermediates,
        # not deliverables, so they live in the state folder rather than
        # cluttering the folder the user uploads from.
        text_dir=state_dir(project_root, create=True) / "text",
        metadata=_metadata(preset, slug),
    )

    plan = render.render(board, preset, options, binaries, dry_run=request.dry_run)

    bundle = report.PostBundle(
        slug=slug, preset=preset, board=board, copy=copy, output=output,
        selection_report=selection_report, music=music,
    )
    sidecars: dict[str, Path] = {}
    if not request.dry_run:
        sidecars = report.write_bundle(bundle)
        ledger.record(
            Post(
                slug=slug, preset=preset.key, created_at=time.time(),
                assets=board.assets, output=str(output), duration=board.duration,
            )
        )
        ledger.save(project_root)

    return MakeResult(bundle=bundle, plan=plan, sidecars=sidecars, skipped=request.dry_run)


def make_batch(
    library: Library,
    config: Config,
    requests: Sequence[MakeRequest],
    project_root: Path | str = ".",
    binaries: ff.Binaries | None = None,
    stop_on_error: bool = False,
) -> tuple[list[MakeResult], list[str]]:
    """Render several posts in sequence, sharing one ledger.

    Sharing the ledger is what makes a batch coherent: post three cannot reuse
    a clip that post one already took, because post one recorded it first.
    """
    binaries = binaries or ff.find_binaries()
    ledger = Ledger.load(project_root)
    results: list[MakeResult] = []
    errors: list[str] = []

    for index, request in enumerate(requests, start=1):
        label = request.slug or request.preset_key
        log.info("[%d/%d] %s", index, len(requests), label)
        try:
            results.append(
                make(library, config, request, project_root, ledger=ledger, binaries=binaries)
            )
        except (PipelineError, render.RenderError, ff.FFmpegError) as exc:
            message = f"{label}: {exc}"
            errors.append(message)
            log.error("%s", message)
            if stop_on_error:
                break

    return results, errors


def plan_week(
    config: Config,
    preset_keys: Sequence[str],
    count: int,
    duration: float,
    music: Path | None = None,
    bpm: float | None = None,
    prefix: str = "",
) -> list[MakeRequest]:
    """Build a batch of requests cycling through *preset_keys*."""
    keys = list(preset_keys) or [config.preset]
    stamp = time.strftime("%Y%m%d")
    requests: list[MakeRequest] = []
    for index in range(count):
        key = keys[index % len(keys)]
        slug = f"{prefix or stamp}-{index + 1:02d}-{key}"
        requests.append(
            MakeRequest(
                preset_key=key, duration=duration, music=music, bpm=bpm, slug=slug
            )
        )
    return requests


def default_slug(preset: Preset) -> str:
    """A dated, unique-enough slug for a single ad-hoc render."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(preset.key)}"


def _seed_from(slug: str) -> int:
    """Derive a stable seed from a slug so a re-render is identical."""
    from .util import stable_seed

    return stable_seed(slug) % (2**31)


def _recent_hooks(ledger: Ledger, preset: Preset) -> list[str]:
    """Hooks used recently for this preset, so the next post opens differently."""
    recent = ledger.recent_hooks(preset.key, limit=len(preset.copy.hooks) or 5)
    used: list[str] = []
    for slug in recent:
        used.append(captions_mod.write(preset, slug).hook)
    return used


def _metadata(preset: Preset, slug: str) -> dict[str, str]:
    """Container metadata written into the MP4.

    Stating the pipeline in the file is useful later: it is a truthful record
    that the video was assembled from supplied footage rather than generated.
    """
    return {
        "title": f"{preset.title} - {slug}",
        "comment": (
            "Assembled by mbtok from user-supplied photos and footage. "
            "No generative AI was used to create any frame."
        ),
        "encoder": "mbtok",
    }
