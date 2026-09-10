"""End-to-end tests that really run ffmpeg.

These are skipped automatically when ffmpeg is not installed, so the rest of
the suite still runs on a machine without it. They are the only tests that
prove the emitted filtergraph is something ffmpeg will actually accept.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mbtok import ffmpeg as ff
from mbtok import library as library_mod
from mbtok import pipeline
from mbtok.cli import main
from mbtok.config import Config

try:
    BINARIES: ff.Binaries | None = ff.find_binaries()
except ff.FFmpegError:
    BINARIES = None

needs_ffmpeg = pytest.mark.skipif(BINARIES is None, reason="ffmpeg is not installed")


def make_image(path: Path, colour: str, width: int, height: int) -> None:
    """Write a small test photograph."""
    subprocess.run(
        [BINARIES.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={colour}:s={width}x{height}",
         "-vf", "noise=alls=18:allf=t", "-frames:v", "1", str(path)],
        check=True,
    )


def make_video(path: Path, colour: str, width: int, height: int, seconds: float) -> None:
    """Write a short test clip."""
    subprocess.run(
        [BINARIES.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={colour}:s={width}x{height}:d={seconds}:r=25",
         "-vf", "noise=alls=14:allf=t", "-c:v", "libx264", "-crf", "30",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def make_music(path: Path, seconds: float = 20.0) -> None:
    """Write a short tone bed to cut against."""
    subprocess.run(
        [BINARIES.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=f=180:d={seconds}", "-c:a", "libmp3lame", "-b:a", "96k", str(path)],
        check=True,
    )


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> Path:
    """A small library of real files, built once for the whole module."""
    if BINARIES is None:
        pytest.skip("ffmpeg is not installed")
    root = tmp_path_factory.mktemp("media")
    folder = root / "lib"
    folder.mkdir()
    shades = ["0xEDE4D6", "0xE4D8C4", "0xDCCDB6", "0xF0E8DA", "0xE8DECB", "0xD8C9B2"]
    for index, colour in enumerate(shades):
        make_image(folder / f"linen-morning-{index}.jpg", colour, 720, 1280)
    make_video(folder / "curtain-motion.mp4", "0xE6DAC6", 720, 1280, 4.0)
    make_video(folder / "coffee-pour.mp4", "0xDFD0BA", 1280, 720, 4.0)
    make_music(root / "track.mp3")
    return root


def probe(path: Path) -> dict:
    """Probe a rendered file."""
    result = subprocess.run(
        [BINARIES.ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout.decode())


def streams_of(data: dict, kind: str) -> list[dict]:
    """Streams of a given type from a probe result."""
    return [s for s in data["streams"] if s.get("codec_type") == kind]


@needs_ffmpeg
def test_scan_reads_real_files(media, tmp_path):
    """A real scan probes and colour-samples every supported file."""
    library = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    assert len(library.usable) == 8
    assert all(asset.palette.swatches for asset in library.usable)
    assert all(asset.quality > 0 for asset in library.usable)
    kinds = {asset.kind for asset in library.usable}
    assert kinds == {"image", "video"}


@needs_ffmpeg
def test_scan_is_incremental(media, tmp_path):
    """A second scan reuses the cached analysis instead of redoing it."""
    first = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    first.save(tmp_path)
    second = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    assert [a.fingerprint for a in second.assets] == [a.fingerprint for a in first.assets]
    assert [a.palette.hexes for a in second.assets] == [a.palette.hexes for a in first.assets]


@needs_ffmpeg
@pytest.mark.parametrize("key", ["clean-girl", "tokyo-night", "street-mono", "y2k-chrome"])
def test_render_produces_a_playable_video(media, tmp_path, key):
    """Four moods exercising grade, bloom, desaturation and fast cuts all encode."""
    library = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    library.save(tmp_path)
    config = Config(output_dir=str(tmp_path / "out"), crf=32, max_bitrate_mbps=4.0)
    request = pipeline.MakeRequest(
        preset_key=key, duration=4.0, bpm=120.0, shots=5, slug=f"it-{key}",
        music=media / "track.mp3",
    )
    result = pipeline.make(library, config, request, tmp_path, binaries=BINARIES)

    output = result.bundle.output
    assert output.exists()
    data = probe(output)

    video = streams_of(data, "video")[0]
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert streams_of(data, "audio"), "music should be muxed in"

    duration = float(data["format"]["duration"])
    assert duration == pytest.approx(result.bundle.board.duration, abs=0.15)


@needs_ffmpeg
def test_rendered_frames_are_not_blank(media, tmp_path):
    """The output carries real picture, not a black or frozen frame."""
    library = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    config = Config(output_dir=str(tmp_path / "out"), crf=32, max_bitrate_mbps=4.0)
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=4.0, bpm=120.0,
                                   shots=4, slug="frames")
    result = pipeline.make(library, config, request, tmp_path, binaries=BINARIES)

    samples = []
    for moment in (0.4, 2.0, 3.4):
        pixels = ff._sample_once(BINARIES, result.bundle.output, 32, seek=moment, timeout=60)
        assert pixels
        average = sum(sum(pixel) for pixel in pixels) / (len(pixels) * 3)
        samples.append(average)
        assert 12 < average < 250, f"frame at {moment}s looks blank"
    # Cuts and the Ken Burns move should make the frames differ from each other.
    assert max(samples) - min(samples) > 0.5


@needs_ffmpeg
def test_silent_render_has_no_audio(media, tmp_path):
    """Rendering without music produces a video-only file."""
    library = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    config = Config(output_dir=str(tmp_path / "out"), crf=34, max_bitrate_mbps=4.0)
    request = pipeline.MakeRequest(preset_key="vanilla-girl", duration=3.0, bpm=100.0,
                                   shots=4, slug="silent")
    result = pipeline.make(library, config, request, tmp_path, binaries=BINARIES)
    assert streams_of(probe(result.bundle.output), "audio") == []


@needs_ffmpeg
def test_batch_never_reuses_an_asset(media, tmp_path):
    """Three posts in one batch draw from disjoint sets of footage."""
    library = library_mod.scan([media / "lib"], project_root=tmp_path, workers=4)
    library.save(tmp_path)
    config = Config(output_dir=str(tmp_path / "out"), crf=34,
                    max_bitrate_mbps=4.0, cooldown_days=21)
    requests = [
        pipeline.MakeRequest(preset_key="clean-girl", duration=2.5, bpm=120.0,
                             shots=2, slug=f"b{index}")
        for index in range(3)
    ]
    results, errors = pipeline.make_batch(library, config, requests, tmp_path, binaries=BINARIES)
    assert errors == []
    assert len(results) == 3

    seen: set[str] = set()
    for result in results:
        assets = set(result.bundle.board.assets)
        assert assets.isdisjoint(seen)
        seen |= assets


@needs_ffmpeg
def test_cli_round_trip(media, tmp_path, capsys):
    """init, scan, boards and make all succeed through the command line."""
    project = str(tmp_path)
    assert main(["--project", project, "init", str(media / "lib"),
                 "--output", str(tmp_path / "out")]) == 0
    assert main(["--project", project, "--quiet", "scan"]) == 0
    assert main(["--project", project, "boards", "--shots", "4"]) == 0
    assert main(["--project", project, "make", "--preset", "coastal-linen",
                 "--duration", "3", "--bpm", "110", "--shots", "4",
                 "--slug", "cli-post", "--crf", "34"]) == 0

    output = tmp_path / "out"
    assert (output / "cli-post.mp4").exists()
    assert (output / "cli-post.caption.txt").read_text(encoding="utf-8").strip()
    record = json.loads((output / "cli-post.provenance.json").read_text(encoding="utf-8"))
    assert record["generative_ai_used"] is False
    assert record["sources"]

    assert main(["--project", project, "status"]) == 0
    assert "cli-post" in capsys.readouterr().out


@needs_ffmpeg
def test_missing_library_is_reported(tmp_path, capsys):
    """Rendering with nothing indexed fails cleanly with an instruction."""
    assert main(["--project", str(tmp_path), "make"]) == 1
    assert "mbtok scan" in capsys.readouterr().err


@needs_ffmpeg
def test_doctor_reports_a_healthy_project(media, tmp_path):
    """After a scan, doctor reports the project as ready."""
    assert main(["--project", str(tmp_path), "init", str(media / "lib")]) == 0
    assert main(["--project", str(tmp_path), "--quiet", "scan"]) == 0
    assert main(["--project", str(tmp_path), "doctor"]) == 0
