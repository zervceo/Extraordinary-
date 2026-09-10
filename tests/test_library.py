"""Library indexing: classification, quality scoring and caching."""

from __future__ import annotations

from pathlib import Path

import pytest

from mbtok import ffmpeg as ff
from mbtok import library as lib
from mbtok.color import Palette


def info(width: int, height: int, duration: float = 6.0) -> ff.MediaInfo:
    """A minimal probe result."""
    return ff.MediaInfo(width=width, height=height, duration=duration)


NEUTRAL = Palette(brightness=0.5, contrast=0.3)


def test_source_is_read_from_the_path():
    """Stock library folders are recognised for the provenance record."""
    cases = {
        "/Users/a/Downloads/Envato Elements/linen.mp4": "envato",
        "/Users/a/Stock/videohive/clip.mp4": "envato",
        "/Users/a/Pictures/DCIM/IMG_0001.jpg": "camera",
        "/Users/a/Downloads/unsplash/beach.jpg": "unsplash",
        "/Users/a/Documents/random.jpg": "local",
    }
    for path, expected in cases.items():
        assert lib.guess_source(Path(path)) == expected


def test_junk_files_are_recognised():
    """Previews, proxies and macOS stubs are skipped."""
    for name in ("clip_preview.mp4", "shot-thumb.jpg", "._IMG.jpg", ".DS_Store",
                 "video_watermark.mp4", "photo_proxy.mov"):
        assert lib.looks_like_junk(Path(name))
    for name in ("linen-morning-01.jpg", "coffee.mp4", "IMG_2231.HEIC"):
        assert not lib.looks_like_junk(Path(name))


def test_media_kinds():
    """Extensions map to the right handling, and unknowns are ignored."""
    assert ff.kind_for(Path("a.HEIC")) == "image"
    assert ff.kind_for(Path("a.MOV")) == "video"
    assert ff.kind_for(Path("a.wav")) == "audio"
    assert ff.kind_for(Path("a.txt")) is None


def test_vertical_beats_landscape_on_quality():
    """A portrait frame survives the crop better than a landscape one."""
    portrait = lib.quality_score(info(1080, 1920), "image", NEUTRAL)
    landscape = lib.quality_score(info(1920, 1080), "image", NEUTRAL)
    assert portrait > landscape


def test_ultrawide_is_penalised_more_than_landscape():
    """The wider the frame, the less of it survives a 9:16 crop."""
    landscape = lib.quality_score(info(1920, 1080), "image", NEUTRAL)
    ultrawide = lib.quality_score(info(3840, 1080), "image", NEUTRAL)
    assert ultrawide < landscape


def test_low_resolution_is_penalised():
    """A small source cannot fill a 1080x1920 frame."""
    assert lib.quality_score(info(640, 480), "image", NEUTRAL) < lib.quality_score(
        info(1080, 1920), "image", NEUTRAL
    )


def test_badly_exposed_frames_are_penalised():
    """Near-black and blown-out frames score below a well-exposed one."""
    good = lib.quality_score(info(1080, 1920), "image", NEUTRAL)
    dark = lib.quality_score(info(1080, 1920), "image", Palette(brightness=0.02, contrast=0.3))
    blown = lib.quality_score(info(1080, 1920), "image", Palette(brightness=0.99, contrast=0.3))
    assert dark < good and blown < good


def test_flat_frames_are_penalised():
    """A frame with almost no contrast is probably a mistake."""
    flat = lib.quality_score(info(1080, 1920), "image", Palette(brightness=0.5, contrast=0.01))
    assert flat < lib.quality_score(info(1080, 1920), "image", NEUTRAL)


def test_very_short_clips_are_penalised():
    """Footage too short to fill a shot scores low."""
    assert lib.quality_score(info(1080, 1920, 0.8), "video", NEUTRAL) < 0.5


def test_quality_is_bounded():
    """The score never leaves the 0-1 range, even for absurd input."""
    for width, height in ((0, 0), (1, 1), (20000, 10), (8000, 8000)):
        assert 0.0 <= lib.quality_score(info(width, height), "image", NEUTRAL) <= 1.0


def test_asset_roundtrips_through_json():
    """An indexed asset survives being written to and read from the index."""
    asset = lib.Asset(
        path="/lib/a.jpg", kind="image", fingerprint="abc",
        width=1080, height=1920, quality=0.9, source="envato",
    )
    restored = lib.Asset.from_dict(asset.to_dict())
    assert restored.path == asset.path
    assert restored.source == "envato"
    assert restored.is_vertical
    assert restored.usable


def test_asset_with_an_error_is_unusable():
    """A file that failed to probe is excluded from selection."""
    asset = lib.Asset(path="/lib/bad.mp4", kind="video", fingerprint="", error="boom")
    assert not asset.usable


def test_library_roundtrips_on_disk(tmp_path):
    """The index survives a save and load cycle."""
    library = lib.Library(
        assets=[
            lib.Asset(path="/lib/a.jpg", kind="image", fingerprint="f",
                      width=1080, height=1920, quality=0.8)
        ],
        roots=["/lib"],
    )
    library.save(tmp_path)
    restored = lib.Library.load(tmp_path)
    assert len(restored.assets) == 1
    assert restored.roots == ["/lib"]


def test_stale_index_version_is_discarded(tmp_path):
    """An index written by an older version is rebuilt rather than trusted."""
    from mbtok.util import write_json

    write_json(tmp_path / ".mbtok" / "library.json", {"version": 0, "assets": [{"path": "/x"}]})
    assert lib.Library.load(tmp_path).assets == []


def test_stats_describe_the_library():
    """Statistics separate stills from footage and count failures."""
    library = lib.Library(
        assets=[
            lib.Asset(path="/a.jpg", kind="image", fingerprint="1", width=1080, height=1920),
            lib.Asset(path="/b.mp4", kind="video", fingerprint="2", width=1920,
                      height=1080, duration=8.0),
            lib.Asset(path="/c.mp4", kind="video", fingerprint="3", error="unreadable"),
        ]
    )
    stats = library.stats()
    assert stats["usable"] == 2
    assert stats["failed"] == 1
    assert stats["by_kind"] == {"image": 1, "video": 1}
    assert stats["vertical"] == 1
    assert stats["footage_seconds"] == pytest.approx(8.0)


def test_walk_skips_noise_directories(tmp_path):
    """Hidden and build directories are not descended into."""
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "a.jpg").write_bytes(b"x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.jpg").write_bytes(b"x")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "c.jpg").write_bytes(b"x")
    found = {path.name for path in lib.walk([tmp_path])}
    assert found == {"a.jpg"}


def test_walk_accepts_a_single_file(tmp_path):
    """Passing a file rather than a folder indexes just that file."""
    target = tmp_path / "one.jpg"
    target.write_bytes(b"x")
    assert [path.name for path in lib.walk([target])] == ["one.jpg"]


def test_walk_deduplicates_overlapping_roots(tmp_path):
    """Nested scan roots do not index the same file twice."""
    (tmp_path / "inner").mkdir()
    (tmp_path / "inner" / "a.jpg").write_bytes(b"x")
    found = list(lib.walk([tmp_path, tmp_path / "inner"]))
    assert len(found) == 1


def test_probe_parsing_handles_rotation():
    """A rotated phone clip reports the size a viewer actually sees."""
    parsed = ff._media_info_from_probe(
        {
            "format": {"duration": "12.5"},
            "streams": [
                {
                    "codec_type": "video", "width": 1920, "height": 1080,
                    "avg_frame_rate": "30000/1001",
                    "side_data_list": [{"rotation": -90}],
                },
                {"codec_type": "audio"},
            ],
        }
    )
    assert (parsed.width, parsed.height) == (1080, 1920)
    assert parsed.is_vertical
    assert parsed.has_audio
    assert parsed.fps == pytest.approx(29.97, abs=0.01)


def test_probe_parsing_survives_missing_fields():
    """A probe result with no video stream does not raise."""
    parsed = ff._media_info_from_probe({"format": {}, "streams": []})
    assert parsed.width == 0
    assert parsed.aspect == 0.0


def test_failures_are_grouped_by_extension():
    """A format-wide problem is reported as a format, not as many files."""
    library = lib.Library(
        assets=[
            lib.Asset(path=f"/a/{i}.HEIC", kind="image", fingerprint="", error="no decoder")
            for i in range(5)
        ]
        + [lib.Asset(path="/a/x.mov", kind="video", fingerprint="", error="broken")]
        + [lib.Asset(path="/a/ok.jpg", kind="image", fingerprint="f",
                     width=1080, height=1920)]
    )
    assert library.failure_breakdown() == {".heic": 5, ".mov": 1}
    assert library.stats()["failed"] == 6


def test_known_formats_get_specific_advice():
    """HEIC failures name the actual fix rather than a generic message."""
    assert "brew" in lib.advice_for(".HEIC")
    assert "verbose" in lib.advice_for(".mov")
