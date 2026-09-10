"""Thin wrapper around the ffmpeg/ffprobe binaries.

mbtok deliberately has no Python image or video dependencies. Everything it
needs to know about a file comes from ``ffprobe``, and every pixel it inspects
is decoded by ``ffmpeg`` into a raw RGB buffer on stdout. That keeps setup on a
Mac down to ``brew install ffmpeg``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger("mbtok.ffmpeg")

#: Container extensions we are willing to hand to ffmpeg.
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff",
    ".bmp", ".avif", ".jxl",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".mpg",
    ".mpeg", ".wmv", ".flv",
}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".aiff", ".aif"}


class FFmpegError(RuntimeError):
    """Raised when ffmpeg or ffprobe is missing, or exits non-zero."""


@dataclass(frozen=True)
class Binaries:
    """Resolved paths to the two binaries we shell out to."""

    ffmpeg: str
    ffprobe: str


def find_binaries(ffmpeg: str | None = None, ffprobe: str | None = None) -> Binaries:
    """Locate ffmpeg and ffprobe, honouring explicit paths and env overrides.

    Raises :class:`FFmpegError` naming the Homebrew install command when either
    binary is missing, because that is the single most common setup failure.
    """
    resolved_ffmpeg = ffmpeg or os.environ.get("MBTOK_FFMPEG") or shutil.which("ffmpeg")
    resolved_ffprobe = ffprobe or os.environ.get("MBTOK_FFPROBE") or shutil.which("ffprobe")
    missing = [
        name
        for name, value in (("ffmpeg", resolved_ffmpeg), ("ffprobe", resolved_ffprobe))
        if not value
    ]
    if missing:
        raise FFmpegError(
            f"could not find {' and '.join(missing)} on PATH. "
            "Install it with:  brew install ffmpeg"
        )
    return Binaries(ffmpeg=str(resolved_ffmpeg), ffprobe=str(resolved_ffprobe))


def kind_for(path: Path) -> str | None:
    """Classify a path as ``image``, ``video``, ``audio`` or ``None``."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


@dataclass
class MediaInfo:
    """The subset of ffprobe output that the rest of the pipeline uses."""

    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    codec: str = ""
    has_audio: bool = False
    creation_time: str | None = None
    rotation: int = 0
    bitrate: int = 0

    @property
    def aspect(self) -> float:
        """Width divided by height, or 0 when the size is unknown."""
        return (self.width / self.height) if self.height else 0.0

    @property
    def is_vertical(self) -> bool:
        """True when the frame is taller than it is wide."""
        return self.height > self.width

    @property
    def megapixels(self) -> float:
        """Frame size in megapixels."""
        return (self.width * self.height) / 1_000_000.0


def run(command: Sequence[str], timeout: float = 300.0, capture: bool = True) -> subprocess.CompletedProcess:
    """Run *command*, raising :class:`FFmpegError` with stderr on failure."""
    log.debug("run: %s", " ".join(command))
    try:
        result = subprocess.run(
            list(command),
            capture_output=capture,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - guarded by find_binaries
        raise FFmpegError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"timed out after {timeout:.0f}s: {' '.join(command[:3])}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
        tail = "\n".join(stderr.splitlines()[-12:])
        raise FFmpegError(f"{command[0]} exited {result.returncode}\n{tail}")
    return result


def probe(binaries: Binaries, path: Path, timeout: float = 60.0) -> MediaInfo:
    """Return a :class:`MediaInfo` for *path* using a single ffprobe call."""
    command = [
        binaries.ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = run(command, timeout=timeout)
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace"))
    except ValueError as exc:
        raise FFmpegError(f"unreadable ffprobe output for {path}") from exc
    return _media_info_from_probe(payload)


def _media_info_from_probe(payload: dict) -> MediaInfo:
    """Fold raw ffprobe JSON into a :class:`MediaInfo`."""
    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    info = MediaInfo(has_audio=audio is not None)
    if fmt.get("duration"):
        info.duration = _safe_float(fmt["duration"])
    if fmt.get("bit_rate"):
        info.bitrate = int(_safe_float(fmt["bit_rate"]))
    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    info.creation_time = tags.get("creation_time")

    if video is None:
        return info

    info.width = int(video.get("width") or 0)
    info.height = int(video.get("height") or 0)
    info.codec = str(video.get("codec_name") or "")
    info.fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0")
    if not info.duration:
        info.duration = _safe_float(video.get("duration") or 0.0)
    info.rotation = _rotation_from_stream(video)
    # A rotated phone clip reports its pre-rotation frame size; swap so every
    # downstream size decision sees the frame the way a viewer sees it.
    if info.rotation in (90, 270):
        info.width, info.height = info.height, info.width
    stream_tags = {str(k).lower(): v for k, v in (video.get("tags") or {}).items()}
    info.creation_time = info.creation_time or stream_tags.get("creation_time")
    return info


def _rotation_from_stream(stream: dict) -> int:
    """Extract display rotation in degrees from tags or side data."""
    tags = {str(k).lower(): v for k, v in (stream.get("tags") or {}).items()}
    if "rotate" in tags:
        return int(_safe_float(tags["rotate"])) % 360
    for entry in stream.get("side_data_list") or []:
        if "rotation" in entry:
            return int(round(-_safe_float(entry["rotation"]))) % 360
    return 0


def _parse_rate(value: str) -> float:
    """Parse an ffprobe rational such as ``30000/1001``."""
    text = str(value)
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        denominator_value = _safe_float(denominator)
        if denominator_value == 0:
            return 0.0
        return _safe_float(numerator) / denominator_value
    return _safe_float(text)


def _safe_float(value: object) -> float:
    """Best-effort float conversion that never raises."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Frame:
    """One decoded thumbnail: raw RGB pixels plus the shape they form.

    The shape matters. Palette extraction only needs the pixels, but deciding
    where to crop a landscape frame needs to know which pixel sat where, so
    the sample preserves the source aspect ratio rather than squashing it to
    a square.
    """

    pixels: list[tuple[int, int, int]]
    columns: int
    rows: int

    def at(self, column: int, row: int) -> tuple[int, int, int]:
        """The pixel at *column*, *row*."""
        return self.pixels[row * self.columns + column]

    @property
    def valid(self) -> bool:
        """True when the buffer actually holds the shape it claims."""
        return (
            self.columns > 0
            and self.rows > 0
            and len(self.pixels) >= self.columns * self.rows
        )


def sample_frames(
    binaries: Binaries,
    path: Path,
    kind: str,
    width: int = 64,
    frames: int = 3,
    duration: float = 0.0,
    timeout: float = 120.0,
) -> list[Frame]:
    """Decode *path* to a handful of small, aspect-correct RGB thumbnails.

    Images yield one frame. Videos are sampled at *frames* evenly spaced seek
    points, so a clip that changes partway through is not judged on its opening
    frame alone. One decode serves both palette extraction and crop analysis.
    """
    seeks: list[float | None] = [None] if kind == "image" else _seek_points(duration, frames)
    result: list[Frame] = []
    for seek in seeks:
        try:
            frame = _sample_once(binaries, path, width, seek=seek, timeout=timeout)
        except FFmpegError:
            # A seek past a truncated tail is not worth failing the whole scan.
            log.debug("sample failed at %.2fs in %s", seek or 0.0, path)
            continue
        if frame.valid:
            result.append(frame)
    return result


def sample_pixels(
    binaries: Binaries,
    path: Path,
    kind: str,
    grid: int = 48,
    frames: int = 3,
    duration: float = 0.0,
    timeout: float = 120.0,
) -> list[tuple[int, int, int]]:
    """Every pixel from :func:`sample_frames`, flattened, for palette work."""
    pixels: list[tuple[int, int, int]] = []
    for frame in sample_frames(
        binaries, path, kind, width=grid, frames=frames, duration=duration, timeout=timeout
    ):
        pixels.extend(frame.pixels)
    return pixels


def _seek_points(duration: float, frames: int) -> list[float | None]:
    """Evenly spaced seek positions that avoid the very first and last frames."""
    if duration <= 0.2 or frames <= 1:
        return [None]
    count = max(1, frames)
    span = duration * 0.9
    start = duration * 0.05
    return [start + span * (index / max(1, count - 1)) for index in range(count)]


def _sample_once(
    binaries: Binaries,
    path: Path,
    width: int,
    seek: float | None,
    timeout: float,
) -> Frame:
    """Decode a single frame to a small aspect-correct raw RGB buffer.

    ``scale=W:-2`` keeps the source proportions and guarantees an even height,
    which the rgb24 unpacking below relies on to reshape the buffer.
    """
    command = [binaries.ffmpeg, "-v", "error", "-nostdin"]
    if seek is not None:
        command += ["-ss", f"{seek:.3f}"]
    command += [
        "-i", str(path),
        "-map", "0:v:0",
        "-frames:v", "1",
        "-vf", f"scale={width}:-2:flags=area,format=rgb24",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]
    result = run(command, timeout=timeout)
    pixels = _unpack_rgb(result.stdout)
    rows = len(pixels) // width if width else 0
    return Frame(pixels=pixels[: width * rows], columns=width, rows=rows)


def _unpack_rgb(buffer: bytes) -> list[tuple[int, int, int]]:
    """Turn a packed rgb24 byte buffer into a list of pixel triples."""
    usable = len(buffer) - (len(buffer) % 3)
    return [
        (buffer[index], buffer[index + 1], buffer[index + 2])
        for index in range(0, usable, 3)
    ]


def decode_audio_mono(
    binaries: Binaries,
    path: Path,
    sample_rate: int = 22050,
    max_seconds: float = 120.0,
    timeout: float = 180.0,
) -> list[float]:
    """Decode *path* to a mono float list in -1..1 for beat analysis."""
    import array

    command = [
        binaries.ffmpeg, "-v", "error", "-nostdin",
        "-i", str(path),
        "-t", f"{max_seconds:.2f}",
        "-map", "0:a:0",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-",
    ]
    result = run(command, timeout=timeout)
    raw = result.stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    return [value / 32768.0 for value in samples]


_ALIGN_SUPPORT: dict[str, bool] = {}


def supports_drawtext_align(binaries: Binaries) -> bool:
    """True when this ffmpeg's ``drawtext`` accepts ``text_align``.

    The option arrived in ffmpeg 6.1. Passing it to an older build is a hard
    error rather than a warning, so multi-line text is centred only when the
    build can actually do it, and left-aligned otherwise.
    """
    cached = _ALIGN_SUPPORT.get(binaries.ffmpeg)
    if cached is not None:
        return cached
    try:
        result = run([binaries.ffmpeg, "-hide_banner", "-h", "filter=drawtext"], timeout=30.0)
        text = result.stdout.decode("utf-8", "replace")
        supported = "text_align" in text
    except FFmpegError:
        supported = False
    _ALIGN_SUPPORT[binaries.ffmpeg] = supported
    return supported


def font_candidates() -> list[str]:
    """Font files worth trying for text overlays, macOS first.

    ``drawtext`` needs a real file path; it cannot look a family up by name.
    """
    return [
        "/System/Library/Fonts/Supplemental/Futura.ttc",
        "/System/Library/Fonts/Supplemental/AvenirNext.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]


def find_font(preferred: Sequence[str] = ()) -> str | None:
    """Return the first readable font file from *preferred* then the defaults."""
    for candidate in list(preferred) + font_candidates():
        if candidate and Path(candidate).exists():
            return candidate
    return None
