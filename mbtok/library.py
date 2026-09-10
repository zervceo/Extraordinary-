"""Scan folders of owned media into a cached, scoreable index.

The index is the expensive part of mbtok: probing and colour-sampling a few
thousand stock clips takes minutes. It is written to ``.mbtok/library.json``
and keyed by a cheap fingerprint, so a rescan only touches files that are new
or changed.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Sequence

from . import ffmpeg as ff
from .color import Palette, analyze
from .crop import Focus, focus_for
from .util import dedupe, expand, fingerprint, read_json, state_dir, write_json

log = logging.getLogger("mbtok.library")

#: Index format version. Bumping it forces a full re-analysis on next scan.
INDEX_VERSION = 4

#: Folders that are never worth walking into.
SKIP_DIRECTORIES = {
    ".git", ".svn", "node_modules", "__pycache__", ".mbtok", "Library",
    ".Trash", ".cache", "venv", ".venv", "renders", "out",
    # Inside a Photos library package: everything except the originals is a
    # derivative, a thumbnail or a database, and indexing any of it would fill
    # the library with low-resolution duplicates of photos already indexed.
    "derivatives", "resources", "database", "private", "external",
}

#: Filename fragments that mark a file as a preview, proxy or thumbnail rather
#: than the asset itself. Envato bundles ship a lot of these.
JUNK_MARKERS = (
    "thumb", "thumbnail", "preview", "watermark", "sample", "proxy",
    "screenshot", "_lr", "contact-sheet",
)


@dataclass
class Asset:
    """One indexed photo or video, plus everything we scored it on."""

    path: str
    kind: str
    fingerprint: str
    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    codec: str = ""
    has_audio: bool = False
    bytes: int = 0
    mtime: float = 0.0
    created: str | None = None
    source: str = "local"
    palette: Palette = field(default_factory=Palette)
    #: Where the interesting part of the frame sits, for content-aware cropping.
    focus: Focus = field(default_factory=Focus)
    quality: float = 0.0
    error: str | None = None

    @property
    def file(self) -> Path:
        """The asset's path as a :class:`Path`."""
        return Path(self.path)

    @property
    def name(self) -> str:
        """Filename without directories."""
        return Path(self.path).name

    @property
    def folder(self) -> str:
        """Containing directory, used to avoid stacking near-identical shots."""
        return str(Path(self.path).parent)

    @property
    def aspect(self) -> float:
        """Width over height, 0 when unknown."""
        return (self.width / self.height) if self.height else 0.0

    @property
    def is_vertical(self) -> bool:
        """True when the frame is portrait."""
        return self.height > self.width

    @property
    def usable(self) -> bool:
        """True when the file probed cleanly and has real pixels."""
        return self.error is None and self.width > 0 and self.height > 0

    def to_dict(self) -> dict:
        """JSON representation for the on-disk index."""
        return {
            "path": self.path,
            "kind": self.kind,
            "fingerprint": self.fingerprint,
            "width": self.width,
            "height": self.height,
            "duration": round(self.duration, 3),
            "fps": round(self.fps, 3),
            "codec": self.codec,
            "has_audio": self.has_audio,
            "bytes": self.bytes,
            "mtime": self.mtime,
            "created": self.created,
            "source": self.source,
            "palette": self.palette.to_dict(),
            "focus": self.focus.to_dict(),
            "quality": round(self.quality, 4),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Asset":
        """Rebuild an asset from :meth:`to_dict` output."""
        return cls(
            path=data["path"],
            kind=data.get("kind", "image"),
            fingerprint=data.get("fingerprint", ""),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            duration=float(data.get("duration", 0.0)),
            fps=float(data.get("fps", 0.0)),
            codec=data.get("codec", ""),
            has_audio=bool(data.get("has_audio", False)),
            bytes=int(data.get("bytes", 0)),
            mtime=float(data.get("mtime", 0.0)),
            created=data.get("created"),
            source=data.get("source", "local"),
            palette=Palette.from_dict(data.get("palette", {})),
            focus=Focus.from_dict(data.get("focus")),
            quality=float(data.get("quality", 0.0)),
            error=data.get("error"),
        )


def guess_source(path: Path) -> str:
    """Label where an asset came from, from its path.

    Knowing an asset is an Envato download matters for the provenance report
    that accompanies every render, so it is recorded at scan time.
    """
    lowered = str(path).lower()
    for marker, label in (
        ("envato", "envato"),
        ("elements", "envato"),
        ("videohive", "envato"),
        ("photodune", "envato"),
        ("artgrid", "artgrid"),
        ("storyblocks", "storyblocks"),
        ("adobe stock", "adobe-stock"),
        ("adobestock", "adobe-stock"),
        ("shutterstock", "shutterstock"),
        ("unsplash", "unsplash"),
        ("pexels", "pexels"),
        ("/photos library", "photos-library"),
        ("photos library.photoslibrary", "photos-library"),
        ("dcim", "camera"),
        ("/camera roll", "camera"),
    ):
        if marker in lowered:
            return label
    return "local"


def looks_like_junk(path: Path) -> bool:
    """True for previews, proxies and macOS resource-fork stubs."""
    name = path.name.lower()
    if name.startswith("._") or name == ".ds_store":
        return True
    stem = path.stem.lower()
    return any(marker in stem for marker in JUNK_MARKERS)


def walk(roots: Sequence[Path], follow_symlinks: bool = False) -> Iterator[Path]:
    """Yield every candidate media file under *roots*, skipping noise."""
    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            if root not in seen:
                seen.add(root)
                yield root
            continue
        if not root.is_dir():
            log.warning("not a folder, skipping: %s", root)
            continue
        for path in _walk_dir(root, follow_symlinks):
            if path not in seen:
                seen.add(path)
                yield path


def _walk_dir(root: Path, follow_symlinks: bool) -> Iterator[Path]:
    """Depth-first walk of *root* that prunes noisy directories.

    Uses ``os.scandir`` rather than ``Path.iterdir``: it reuses the directory
    entry's own type information instead of calling ``stat`` per file, which is
    the difference between seconds and minutes over a large photo library, and
    its ``follow_symlinks`` argument works on every supported Python version.
    """
    import os

    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            if entry.name in SKIP_DIRECTORIES or entry.name.startswith("."):
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=follow_symlinks):
                            yield Path(entry.path)
                    except OSError:
                        continue
        except (OSError, PermissionError) as exc:
            log.debug("cannot read %s: %s", current, exc)
            continue


def quality_score(info: ff.MediaInfo, kind: str, palette: Palette) -> float:
    """A 0-1 "is this worth putting in a video" score.

    It rewards resolution that survives a 1080x1920 crop, penalises frames
    that are so wide they would be gutted by that crop, and penalises images
    that are nearly black, blown out, or flat enough to read as a mistake.
    """
    if info.width <= 0 or info.height <= 0:
        return 0.0

    # Resolution: 1080x1920 needs about 2MP, and there is little payoff above 8MP.
    pixels = info.width * info.height
    resolution = min(1.0, pixels / 2_073_600.0)
    if pixels < 700_000:
        resolution *= 0.55

    # Crop survival: how much of the frame is left after a vertical centre crop.
    aspect = info.width / info.height
    if aspect <= 9 / 16:
        crop_penalty = 1.0
    else:
        # Applying the floor after the exponent matters: an ultra-wide frame
        # keeps far less of itself through a 9:16 crop than a 16:9 one does.
        crop_penalty = max(0.30, ((9 / 16) / aspect) ** 0.45)

    exposure = 1.0 - min(1.0, abs(palette.brightness - 0.52) / 0.52) * 0.6
    if palette.brightness < 0.06 or palette.brightness > 0.96:
        exposure *= 0.3
    flatness = 1.0 if palette.contrast > 0.08 else 0.45

    duration_factor = 1.0
    if kind == "video":
        if info.duration < 1.2:
            duration_factor = 0.4
        elif info.duration < 2.5:
            duration_factor = 0.8

    score = resolution * 0.4 + crop_penalty * 0.2 + exposure * 0.25 + flatness * 0.15
    return max(0.0, min(1.0, score * duration_factor))


def analyze_file(
    binaries: ff.Binaries,
    path: Path,
    grid: int = 48,
    video_frames: int = 3,
) -> Asset:
    """Probe and colour-sample one file into an :class:`Asset`."""
    kind = ff.kind_for(path) or "image"
    stat = path.stat()
    asset = Asset(
        path=str(path),
        kind=kind,
        fingerprint=fingerprint(path),
        bytes=stat.st_size,
        mtime=stat.st_mtime,
        source=guess_source(path),
    )
    try:
        info = ff.probe(binaries, path)
    except ff.FFmpegError as exc:
        asset.error = str(exc).splitlines()[0][:200]
        return asset

    asset.width = info.width
    asset.height = info.height
    asset.duration = info.duration if kind == "video" else 0.0
    asset.fps = info.fps
    asset.codec = info.codec
    asset.has_audio = info.has_audio
    asset.created = info.creation_time

    if asset.width <= 0 or asset.height <= 0:
        asset.error = "no video stream"
        return asset

    try:
        frames = ff.sample_frames(
            binaries, path, kind, width=grid, frames=video_frames, duration=asset.duration
        )
    except ff.FFmpegError as exc:
        asset.error = str(exc).splitlines()[0][:200]
        return asset

    pixels = [pixel for frame in frames for pixel in frame.pixels]
    if not pixels:
        asset.error = "no pixels decoded"
        return asset

    asset.palette = analyze(pixels)
    # One decode serves both jobs: the colours it is made of, and where in the
    # frame the interesting part sits.
    asset.focus = focus_for(frames)
    asset.quality = quality_score(info, kind, asset.palette)
    return asset


@dataclass
class Library:
    """The indexed collection, with load/save and incremental scanning."""

    assets: list[Asset] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    scanned_at: float = 0.0
    version: int = INDEX_VERSION

    @property
    def usable(self) -> list[Asset]:
        """Assets that probed cleanly."""
        return [asset for asset in self.assets if asset.usable]

    def by_path(self) -> dict[str, Asset]:
        """Index of assets keyed by absolute path."""
        return {asset.path: asset for asset in self.assets}

    @classmethod
    def load(cls, root: Path | str = ".") -> "Library":
        """Read the cached index, returning an empty one when absent or stale."""
        data = read_json(state_dir(root) / "library.json")
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            return cls()
        return cls(
            assets=[Asset.from_dict(entry) for entry in data.get("assets", [])],
            roots=list(data.get("roots", [])),
            scanned_at=float(data.get("scanned_at", 0.0)),
            version=INDEX_VERSION,
        )

    def save(self, root: Path | str = ".") -> Path:
        """Write the index to ``.mbtok/library.json`` and return its path."""
        path = state_dir(root, create=True) / "library.json"
        write_json(
            path,
            {
                "version": INDEX_VERSION,
                "scanned_at": self.scanned_at or time.time(),
                "roots": self.roots,
                "assets": [asset.to_dict() for asset in self.assets],
            },
        )
        return path

    def stats(self) -> dict:
        """Counts and breakdowns for the ``scan`` and ``doctor`` commands."""
        usable = self.usable
        by_kind: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for asset in usable:
            by_kind[asset.kind] = by_kind.get(asset.kind, 0) + 1
            by_source[asset.source] = by_source.get(asset.source, 0) + 1
        vertical = sum(1 for asset in usable if asset.is_vertical)
        footage = sum(asset.duration for asset in usable if asset.kind == "video")
        return {
            "total": len(self.assets),
            "usable": len(usable),
            "failed": len(self.assets) - len(usable),
            "by_kind": by_kind,
            "by_source": by_source,
            "vertical": vertical,
            "footage_seconds": round(footage, 1),
            "failed_extensions": self.failure_breakdown(),
        }

    def failure_breakdown(self) -> dict[str, int]:
        """Unreadable files counted by extension.

        Failures cluster by format far more than by file. A Mac library full of
        HEIC photos and an ffmpeg built without libheif is the common case, and
        "142 files could not be read" is a much less useful message than
        ".heic: 142".
        """
        counts: dict[str, int] = {}
        for asset in self.assets:
            if asset.error:
                suffix = Path(asset.path).suffix.lower() or "(none)"
                counts[suffix] = counts.get(suffix, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))


def scan(
    roots: Sequence[str | Path],
    project_root: Path | str = ".",
    workers: int = 4,
    grid: int = 48,
    video_frames: int = 3,
    force: bool = False,
    binaries: ff.Binaries | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
    limit: int | None = None,
) -> Library:
    """Index every media file under *roots*, reusing cached analysis.

    Files whose fingerprint matches the cached entry are carried over
    untouched; everything else is probed in a thread pool, since the work is
    dominated by waiting on ffmpeg subprocesses.
    """
    binaries = binaries or ff.find_binaries()
    resolved_roots = [expand(root) for root in roots]
    cached = Library.load(project_root).by_path() if not force else {}

    candidates: list[Path] = []
    for path in walk(resolved_roots):
        if ff.kind_for(path) in ("image", "video") and not looks_like_junk(path):
            candidates.append(path)
        if limit and len(candidates) >= limit:
            break

    fresh: list[Asset] = []
    pending: list[Path] = []
    for path in candidates:
        existing = cached.get(str(path))
        if existing is not None and _unchanged(existing, path):
            fresh.append(existing)
        else:
            pending.append(path)

    total = len(pending)
    log.info("%d files found, %d already indexed, %d to analyse",
             len(candidates), len(fresh), total)

    if total:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_safe_analyze, binaries, path, grid, video_frames): path
                for path in pending
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                asset = future.result()
                fresh.append(asset)
                if progress:
                    progress(index, total, Path(asset.path))

    fresh.sort(key=lambda asset: asset.path)
    library = Library(
        assets=fresh,
        roots=[str(root) for root in resolved_roots],
        scanned_at=time.time(),
    )
    return library


def _safe_analyze(
    binaries: ff.Binaries, path: Path, grid: int, video_frames: int
) -> Asset:
    """Analyse one file, converting any unexpected failure into an error asset."""
    try:
        return analyze_file(binaries, path, grid=grid, video_frames=video_frames)
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill a scan
        log.debug("analysis failed for %s: %s", path, exc)
        try:
            stat = path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        return Asset(
            path=str(path),
            kind=ff.kind_for(path) or "image",
            fingerprint="",
            bytes=size,
            mtime=mtime,
            source=guess_source(path),
            error=f"{type(exc).__name__}: {exc}"[:200],
        )


def _unchanged(asset: Asset, path: Path) -> bool:
    """True when the cached entry still matches the file on disk."""
    try:
        stat = path.stat()
    except OSError:
        return False
    if asset.bytes != stat.st_size:
        return False
    if abs(asset.mtime - stat.st_mtime) > 1.0:
        return False
    return bool(asset.fingerprint)


#: Extensions whose failure has a known, specific remedy.
FORMAT_ADVICE = {
    ".heic": (
        "your ffmpeg was built without HEIC support. Reinstall it with "
        "'brew reinstall ffmpeg', or export those photos as JPEG from Photos"
    ),
    ".heif": (
        "your ffmpeg was built without HEIF support. Reinstall it with "
        "'brew reinstall ffmpeg', or export those photos as JPEG from Photos"
    ),
    ".avif": "your ffmpeg was built without AVIF support. Try 'brew reinstall ffmpeg'",
    ".jxl": "JPEG XL needs a recent ffmpeg. Try 'brew reinstall ffmpeg'",
}


def advice_for(extension: str) -> str:
    """A specific remedy for a failing format, or a generic note."""
    return FORMAT_ADVICE.get(
        extension.lower(), "these files could not be decoded; run with --verbose for details"
    )


def photos_library_roots(home: Path | None = None) -> list[Path]:
    """The ``originals`` folder inside each macOS Photos library.

    Most of a Mac's photographs live inside a ``.photoslibrary`` package rather
    than loose in Pictures. The full-resolution files sit in ``originals`` and
    are ordinary readable files, so they can be indexed in place with no export
    step. Everything else in the package is thumbnails and databases, which
    :data:`SKIP_DIRECTORIES` prunes.

    They are usually HEIC. If your ffmpeg was built without HEIC support the
    scan will say so, per :func:`advice_for`.
    """
    base = (home or Path.home()) / "Pictures"
    if not base.is_dir():
        return []
    roots: list[Path] = []
    try:
        packages = sorted(base.glob("*.photoslibrary"))
    except OSError:
        return []
    for package in packages:
        originals = package / "originals"
        if originals.is_dir():
            roots.append(originals)
    return roots


def default_roots(home: Path | None = None) -> list[Path]:
    """Folders worth scanning on a Mac when the user names none."""
    base = home or Path.home()
    candidates = [
        base / "Downloads",
        base / "Pictures",
        base / "Movies",
        base / "Desktop",
        base / "Documents" / "Envato",
        base / "Downloads" / "Envato Elements",
    ]
    roots = [path for path in candidates if path.is_dir()]
    # Pictures is already listed, but the walk skips nothing about a package
    # name, so name the originals folder explicitly to be sure it is reached.
    return dedupe(roots + photos_library_roots(base))
