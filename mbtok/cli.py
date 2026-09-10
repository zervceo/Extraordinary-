"""Command line interface for mbtok."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from . import curate, ffmpeg as ff, library as library_mod, presets, render, report
from . import pipeline
from .config import Config
from .ledger import Ledger
from .util import expand, human_duration

log = logging.getLogger("mbtok")

PROJECT_ROOT = Path(".")


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def _supports_colour(stream) -> bool:
    """True when it is safe to emit ANSI colour."""
    return hasattr(stream, "isatty") and stream.isatty()


class Printer:
    """Minimal styled console output, quiet by default.

    The streams are resolved on every write rather than captured once, so
    output follows ``sys.stdout`` wherever a caller has redirected it. Colour
    is decided the same way, which keeps ANSI codes out of piped output.
    """

    def __init__(self, stream=None) -> None:
        self._stream = stream

    @property
    def stream(self):
        """The stream to write to right now."""
        return self._stream if self._stream is not None else sys.stdout

    def _wrap(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if _supports_colour(self.stream) else text

    def title(self, text: str) -> None:
        """A section heading."""
        print(self._wrap(text, "1"), file=self.stream)

    def line(self, text: str = "") -> None:
        """A plain line."""
        print(text, file=self.stream)

    def ok(self, text: str) -> None:
        """A success line."""
        print(self._wrap(f"  {text}", "32"), file=self.stream)

    def warn(self, text: str) -> None:
        """A warning line."""
        print(self._wrap(f"  {text}", "33"), file=self.stream)

    def fail(self, text: str) -> None:
        """An error line, on stderr."""
        print(self._wrap(f"  {text}", "31"), file=sys.stderr)

    def dim(self, text: str) -> None:
        """A secondary line."""
        print(self._wrap(f"  {text}", "2"), file=self.stream)


OUT = Printer()


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """Create the project config, guessing sensible scan folders."""
    root = Path(args.project).resolve()
    config = Config.load(root)

    roots = [str(expand(path)) for path in args.roots] if args.roots else []
    if not roots:
        roots = [str(path) for path in library_mod.default_roots()]
    config.roots = roots or config.roots
    if args.output:
        config.output_dir = args.output
    if args.music_dir:
        config.music_dir = str(expand(args.music_dir))
    if args.handle:
        config.handle = args.handle
    if args.preset:
        config.preset = presets.get(args.preset).key

    path = config.save(root)
    OUT.title(f"mbtok project ready in {root}")
    OUT.ok(f"config written to {path}")
    if config.roots:
        OUT.line("  scanning these folders:")
        for entry in config.roots:
            OUT.dim(f"    {entry}")
    else:
        OUT.warn("no media folders configured. Add some with: mbtok scan <folder>")
    OUT.line()
    OUT.line("  Next:  mbtok scan          (index your media)")
    OUT.line("         mbtok boards        (see which moods your library supports)")
    OUT.line("         mbtok make          (render a post)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the environment and report anything that would block a render."""
    root = Path(args.project).resolve()
    OUT.title("mbtok doctor")
    problems = 0

    try:
        binaries = ff.find_binaries()
        version = ff.run([binaries.ffmpeg, "-version"]).stdout.decode("utf-8", "replace")
        OUT.ok(f"ffmpeg: {version.splitlines()[0]}")
    except ff.FFmpegError as exc:
        OUT.fail(f"ffmpeg: {exc}")
        problems += 1
        binaries = None

    if binaries:
        missing = _missing_filters(binaries)
        if missing:
            OUT.fail(f"ffmpeg is missing filters mbtok needs: {', '.join(missing)}")
            problems += 1
        else:
            OUT.ok("ffmpeg has every filter mbtok uses")

    font = ff.find_font()
    if font:
        OUT.ok(f"font for text overlays: {font}")
    else:
        OUT.warn("no font file found; videos will render without text overlays")

    config = Config.load(root)
    if config.roots:
        for entry in config.roots:
            path = expand(entry)
            if path.is_dir():
                OUT.ok(f"media folder: {path}")
            else:
                OUT.warn(f"media folder is missing: {path}")
    else:
        OUT.warn("no media folders configured. Run: mbtok init")

    library = library_mod.Library.load(root)
    if library.assets:
        stats = library.stats()
        OUT.ok(
            f"library: {stats['usable']} usable assets "
            f"({stats['by_kind'].get('image', 0)} photos, "
            f"{stats['by_kind'].get('video', 0)} clips, "
            f"{stats['footage_seconds']:.0f}s of footage)"
        )
        if stats["failed"]:
            OUT.warn(f"{stats['failed']} files could not be read:")
            for extension, count in stats["failed_extensions"].items():
                OUT.dim(f"    {extension}  x{count}  -  {library_mod.advice_for(extension)}")
    else:
        OUT.warn("library is empty. Run: mbtok scan")
        problems += 1

    if config.music_dir:
        music = expand(config.music_dir)
        tracks = (
            [p for p in music.iterdir() if ff.kind_for(p) == "audio"] if music.is_dir() else []
        )
        if tracks:
            OUT.ok(f"music: {len(tracks)} tracks in {music}")
        else:
            OUT.warn(f"no audio files in {music}")
    else:
        OUT.warn("no music folder set; videos render silent unless you pass --music")

    OUT.line()
    if problems:
        OUT.fail(f"{problems} problem(s) need attention before rendering.")
        return 1
    OUT.ok("ready to render.")
    return 0


def _missing_filters(binaries: ff.Binaries) -> list[str]:
    """Filters mbtok depends on that this ffmpeg build does not provide."""
    required = [
        "xfade", "zoompan", "colorbalance", "vignette", "noise", "drawtext",
        "gblur", "tpad", "blend", "eq", "scale", "crop", "afade", "atrim",
    ]
    try:
        listing = ff.run([binaries.ffmpeg, "-hide_banner", "-filters"]).stdout
    except ff.FFmpegError:
        return []
    text = listing.decode("utf-8", "replace")
    available = {line.split()[1] for line in text.splitlines() if len(line.split()) > 2}
    return [name for name in required if name not in available]


def cmd_scan(args: argparse.Namespace) -> int:
    """Index media folders into the library."""
    root = Path(args.project).resolve()
    config = Config.load(root)
    roots = [expand(path) for path in args.roots] if args.roots else config.resolved_roots()

    if not roots:
        OUT.fail("no folders to scan. Pass them as arguments or run: mbtok init")
        return 1

    OUT.title("Scanning")
    for entry in roots:
        OUT.dim(f"  {entry}")

    started = time.time()
    state = {"last": 0.0}

    def progress(done: int, total: int, path: Path) -> None:
        now = time.time()
        if now - state["last"] < 0.2 and done != total:
            return
        state["last"] = now
        width = shutil.get_terminal_size((80, 20)).columns
        message = f"  {done}/{total}  {path.name}"
        print(message[: width - 1].ljust(width - 1), end="\r", file=sys.stdout, flush=True)

    library = library_mod.scan(
        roots,
        project_root=root,
        workers=args.workers,
        force=args.force,
        progress=progress if (not args.quiet and _supports_colour(sys.stdout)) else None,
        limit=args.limit,
    )
    print(" " * (shutil.get_terminal_size((80, 20)).columns - 1), end="\r")

    if not config.roots:
        config.roots = [str(path) for path in roots]
        config.save(root)
    library.save(root)

    stats = library.stats()
    OUT.line()
    OUT.ok(f"indexed {stats['usable']} assets in {time.time() - started:.1f}s")
    OUT.dim(
        f"  {stats['by_kind'].get('image', 0)} photos, "
        f"{stats['by_kind'].get('video', 0)} clips, "
        f"{stats['footage_seconds']:.0f}s of footage, "
        f"{stats['vertical']} already vertical"
    )
    if stats["by_source"]:
        breakdown = ", ".join(
            f"{count} {name}" for name, count in sorted(stats["by_source"].items())
        )
        OUT.dim(f"  sources: {breakdown}")
    if stats["failed"]:
        OUT.warn(f"{stats['failed']} files could not be read:")
        for extension, count in stats["failed_extensions"].items():
            OUT.dim(f"    {extension}  x{count}  -  {library_mod.advice_for(extension)}")
        if args.verbose:
            for asset in library.assets:
                if asset.error:
                    OUT.dim(f"    {asset.name}: {asset.error}")
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    """List the available moods."""
    OUT.title("Moods")
    for key in presets.names():
        preset = presets.get(key)
        OUT.line(f"  {key:18} {preset.title}")
        OUT.dim(f"    {preset.blurb}")
        if args.verbose:
            OUT.dim(f"    palette  {' '.join(preset.palette_hex)}")
            OUT.dim(f"    music    {preset.music}")
    return 0


def cmd_boards(args: argparse.Namespace) -> int:
    """Score the library against every mood, so the user can see what fits."""
    root = Path(args.project).resolve()
    config = Config.load(root)
    library = library_mod.Library.load(root)
    if not library.usable:
        OUT.fail("library is empty. Run: mbtok scan")
        return 1

    ledger = Ledger.load(root)
    cooling = sorted(ledger.cooling(config.cooldown_days))
    keys = [args.preset] if args.preset else presets.names()

    OUT.title(f"Mood fit across {len(library.usable)} assets")
    OUT.line()
    rows: list[tuple[float, str]] = []
    for key in keys:
        preset = presets.get(key)
        selection, selection_report = curate.select(
            library.usable, preset, args.shots,
            exclude=cooling, min_quality=config.min_quality, diversity=config.diversity,
        )
        if not selection:
            rows.append((0.0, f"  {key:18}  no usable material"))
            continue
        summary = curate.summarize(selection)
        rows.append(
            (
                summary["mean_score"],
                f"  {key:18}  fit {summary['mean_score']:.2f}   "
                f"{summary['photos']} photos + {summary['videos']} clips   "
                f"{_bar(summary['mean_score'])}",
            )
        )

    for _, text in sorted(rows, key=lambda row: row[0], reverse=True):
        OUT.line(text)

    OUT.line()
    if cooling:
        OUT.dim(f"  {len(cooling)} assets are resting inside the {config.cooldown_days:g}-day cooldown")
    best = max(rows, key=lambda row: row[0])[1].split()[0] if rows else ""
    if best:
        OUT.dim(f"  Try:  mbtok make --preset {best}")

    if args.preset and args.show:
        _show_selection(library, config, cooling, args)
    return 0


def _bar(value: float, width: int = 20) -> str:
    """A tiny text meter for a 0-1 value."""
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * filled + "-" * (width - filled)


def _show_selection(library, config, cooling, args) -> None:
    """Print the shots a mood would actually choose."""
    preset = presets.get(args.preset)
    selection, _ = curate.select(
        library.usable, preset, args.shots,
        exclude=cooling, min_quality=config.min_quality, diversity=config.diversity,
    )
    OUT.line()
    OUT.title(f"{preset.title} would use:")
    for item in curate.order_shots(selection):
        asset = item.asset
        OUT.line(
            f"  {item.score:.2f}  {asset.kind:5}  {asset.palette.hexes[0] if asset.palette.swatches else '-':8}  {asset.name}"
        )


def cmd_preview(args: argparse.Namespace) -> int:
    """Render a contact sheet of a board, without encoding a video."""
    root = Path(args.project).resolve()
    config = _config_with_overrides(root, args)
    library = library_mod.Library.load(root)
    if not library.usable:
        OUT.fail("library is empty. Run: mbtok scan")
        return 1

    request = pipeline.MakeRequest(
        preset_key=args.preset or config.preset,
        duration=args.duration or config.duration,
        bpm=args.bpm,
        shots=args.shots,
        slug=args.slug or "",
        seed=args.seed,
    )
    output = Path(args.out) if args.out else (
        config.resolved_output(root) / f"preview-{request.preset_key}.png"
    )

    try:
        path, bundle = pipeline.preview(
            library, config, request, output, project_root=root, thumb_width=args.thumb_width
        )
    except (pipeline.PipelineError, render.RenderError, ff.FFmpegError) as exc:
        OUT.fail(str(exc))
        return 1

    OUT.ok(f"contact sheet: {path}")
    OUT.dim(f"  {len(bundle.board.shots)} shots, {bundle.preset.title}")
    for index, shot in enumerate(bundle.board.shots, start=1):
        swatch = shot.asset.palette.hexes[0] if shot.asset.palette.swatches else "-"
        OUT.dim(f"  {index:2}. {swatch:8} {shot.asset.name}")
    OUT.line()
    OUT.dim("  Happy with it?  mbtok make --preset "
            f"{bundle.preset.key} --slug {bundle.slug}")
    return 0


def cmd_make(args: argparse.Namespace) -> int:
    """Render one post."""
    root = Path(args.project).resolve()
    config = _config_with_overrides(root, args)
    library = library_mod.Library.load(root)
    if not library.usable:
        OUT.fail("library is empty. Run: mbtok scan")
        return 1

    request = pipeline.MakeRequest(
        preset_key=args.preset or config.preset,
        duration=args.duration or config.duration,
        music=Path(args.music) if args.music else None,
        bpm=args.bpm,
        slug=args.slug or "",
        hook=args.hook,
        shots=args.shots,
        seed=args.seed,
        no_text=args.no_text,
        dry_run=args.dry_run,
    )

    try:
        result = pipeline.make(library, config, request, project_root=root)
    except (pipeline.PipelineError, render.RenderError, ff.FFmpegError) as exc:
        OUT.fail(str(exc))
        return 1

    _report_result(result, dry_run=args.dry_run)
    if not args.dry_run:
        report.write_plan(
            [result.bundle],
            config.resolved_output(root) / f"{result.bundle.slug}.plan.md",
            title=f"{result.bundle.preset.title} post",
        )
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """Render several posts in one go."""
    root = Path(args.project).resolve()
    config = _config_with_overrides(root, args)
    library = library_mod.Library.load(root)
    if not library.usable:
        OUT.fail("library is empty. Run: mbtok scan")
        return 1

    preset_keys = args.presets or [args.preset or config.preset]
    for key in preset_keys:
        presets.get(key)

    requests = pipeline.plan_week(
        config, preset_keys, args.count,
        duration=args.duration or config.duration,
        music=Path(args.music) if args.music else None,
        bpm=args.bpm,
        prefix=args.prefix or "",
    )
    for request in requests:
        request.dry_run = args.dry_run
        request.no_text = args.no_text

    OUT.title(f"Rendering {len(requests)} posts")
    results, errors = pipeline.make_batch(library, config, requests, project_root=root)

    for result in results:
        _report_result(result, dry_run=args.dry_run, compact=True)

    if results and not args.dry_run:
        plan_path = report.write_plan(
            [result.bundle for result in results],
            config.resolved_output(root) / "posting-plan.md",
            title=f"{len(results)} posts",
        )
        OUT.line()
        OUT.ok(f"posting plan: {plan_path}")

    if errors:
        OUT.line()
        for message in errors:
            OUT.fail(message)
        return 1 if not results else 0
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show what has been posted and what is resting."""
    root = Path(args.project).resolve()
    config = Config.load(root)
    ledger = Ledger.load(root)
    library = library_mod.Library.load(root)

    summary = ledger.summary()
    OUT.title("Status")
    OUT.line(f"  library      {len(library.usable)} usable assets")
    OUT.line(f"  posts made   {summary['posts']} ({summary['posts_last_7_days']} in the last 7 days)")
    OUT.line(f"  assets used  {summary['assets_used']}")
    cooling = ledger.cooling(config.cooldown_days)
    OUT.line(f"  resting      {len(cooling)} assets inside the {config.cooldown_days:g}-day cooldown")
    available = len([a for a in library.usable if a.path not in cooling])
    OUT.line(f"  available    {available} assets ready to use now")

    if summary["by_preset"]:
        OUT.line()
        OUT.title("  By mood")
        for key, count in sorted(summary["by_preset"].items(), key=lambda kv: -kv[1]):
            OUT.line(f"    {key:18} {count}")

    recent = sorted(ledger.posts, key=lambda post: post.created_at, reverse=True)[:10]
    if recent:
        OUT.line()
        OUT.title("  Recent posts")
        for post in recent:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(post.created_at))
            OUT.line(f"    {when}  {post.preset:16} {human_duration(post.duration)}  {post.slug}")
    return 0


def _config_with_overrides(root: Path, args: argparse.Namespace) -> Config:
    """Load the config and apply any command-line overrides."""
    config = Config.load(root)
    if getattr(args, "output", None):
        config.output_dir = args.output
    if getattr(args, "cooldown", None) is not None:
        config.cooldown_days = args.cooldown
    if getattr(args, "min_quality", None) is not None:
        config.min_quality = args.min_quality
    if getattr(args, "diversity", None) is not None:
        config.diversity = args.diversity
    if getattr(args, "font", None):
        config.font = args.font
    if getattr(args, "crf", None) is not None:
        config.crf = args.crf
    return config


def _report_result(result: pipeline.MakeResult, dry_run: bool, compact: bool = False) -> None:
    """Print what a single render produced."""
    bundle = result.bundle
    board = bundle.board
    summary = f"{human_duration(board.duration)} / {len(board.shots)} shots"

    if dry_run:
        OUT.line()
        OUT.title(f"{bundle.slug} (dry run)")
        OUT.dim(f"  {summary}")
        OUT.line()
        OUT.line(result.plan.shell)
        return

    OUT.ok(f"{bundle.output.name}  {summary}  hook: \"{bundle.copy.hook}\"")
    if compact:
        return
    OUT.dim(f"  caption:  {bundle.copy.full_caption.splitlines()[0]}")
    OUT.dim(f"  hashtags: {' '.join(bundle.copy.hashtags)}")
    OUT.dim(f"  post at:  {bundle.copy.posting_window}")
    for path in result.sidecars.values():
        OUT.dim(f"  wrote:    {path.name}")
    for warning in board.warnings:
        OUT.warn(warning)


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the full argument parser."""
    parser = argparse.ArgumentParser(
        prog="mbtok",
        description=(
            "Build aesthetic mood board videos for TikTok from photos and footage "
            "you already own. No generative AI is used at any point."
        ),
    )
    parser.add_argument("--version", action="version", version=f"mbtok {__version__}")
    parser.add_argument("--project", default=".", help="project folder (default: here)")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="set up a project in this folder")
    init.add_argument("roots", nargs="*", help="folders holding your photos and footage")
    init.add_argument("--output", help="where to write videos (default: out)")
    init.add_argument("--music-dir", help="folder of music tracks")
    init.add_argument("--handle", help="sign-off text, e.g. your @handle")
    init.add_argument("--preset", help="default mood")
    init.set_defaults(func=cmd_init)

    doctor = subparsers.add_parser("doctor", help="check that everything is ready")
    doctor.set_defaults(func=cmd_doctor)

    scan = subparsers.add_parser("scan", help="index your media folders")
    scan.add_argument("roots", nargs="*", help="folders to scan (default: configured roots)")
    scan.add_argument("--workers", type=int, default=4, help="parallel ffprobe workers")
    scan.add_argument("--force", action="store_true", help="re-analyse everything")
    scan.add_argument("--limit", type=int, help="stop after this many files")
    scan.set_defaults(func=cmd_scan)

    preset_list = subparsers.add_parser("presets", help="list the available moods")
    preset_list.set_defaults(func=cmd_presets)

    boards = subparsers.add_parser("boards", help="see which moods your library supports")
    boards.add_argument("--preset", help="score a single mood")
    boards.add_argument("--shots", type=int, default=8, help="shots per board")
    boards.add_argument("--show", action="store_true", help="list the shots that would be used")
    boards.set_defaults(func=cmd_boards)

    preview = subparsers.add_parser(
        "preview", help="see a board as a contact sheet, without rendering video"
    )
    _add_render_arguments(preview)
    preview.add_argument("--slug", help="name for the board")
    preview.add_argument("--seed", type=int, help="fix the random seed")
    preview.add_argument("--out", help="where to write the PNG")
    preview.add_argument("--thumb-width", type=int, default=270, dest="thumb_width",
                         help="thumbnail width in pixels")
    preview.set_defaults(func=cmd_preview)

    make = subparsers.add_parser("make", help="render one post")
    _add_render_arguments(make)
    make.add_argument("--slug", help="output filename stem")
    make.add_argument("--hook", help="override the on-screen hook text")
    make.add_argument("--seed", type=int, help="fix the random seed")
    make.set_defaults(func=cmd_make)

    batch = subparsers.add_parser("batch", help="render several posts at once")
    _add_render_arguments(batch)
    batch.add_argument("--count", type=int, default=7, help="how many posts")
    batch.add_argument("--presets", nargs="+", help="moods to cycle through")
    batch.add_argument("--prefix", help="filename prefix (default: today's date)")
    batch.set_defaults(func=cmd_batch)

    status = subparsers.add_parser("status", help="what you have posted and what is resting")
    status.set_defaults(func=cmd_status)

    return parser


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments shared by ``make`` and ``batch``."""
    parser.add_argument("--preset", help="mood to build")
    parser.add_argument("--duration", type=float, help="target length in seconds")
    parser.add_argument("--music", help="audio file to cut to")
    parser.add_argument("--bpm", type=float, help="exact tempo, skipping detection")
    parser.add_argument("--shots", type=int, help="number of shots")
    parser.add_argument("--output", help="output folder")
    parser.add_argument("--cooldown", type=float, help="days before an asset can repeat")
    parser.add_argument("--min-quality", type=float, dest="min_quality",
                        help="lowest acceptable asset quality, 0-1")
    parser.add_argument("--diversity", type=float, help="how hard to push for variety, 0-1")
    parser.add_argument("--font", help="font file for text overlays")
    parser.add_argument("--crf", type=int, help="x264 quality, lower is better")
    parser.add_argument("--no-text", action="store_true", dest="no_text",
                        help="render without text overlays")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="print the ffmpeg command without running it")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")
    # ffmpeg command lines are long and only useful when actively debugging.
    logging.getLogger("mbtok.ffmpeg").setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    if not hasattr(args, "verbose"):
        args.verbose = False

    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        OUT.fail("interrupted")
        return 130
    except ff.FFmpegError as exc:
        OUT.fail(str(exc))
        return 1
    except KeyError as exc:
        OUT.fail(str(exc).strip("'"))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
