"""Orchestration, config and reporting. No ffmpeg is invoked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mbtok import ffmpeg as ff
from mbtok import captions, pipeline, presets, report, storyboard
from mbtok.audio import fixed_grid
from mbtok.config import Config
from mbtok.curate import SelectionReport
from mbtok.ledger import Ledger, Post
from mbtok.library import Library
from tests.conftest import make_asset, make_scored

BINARIES = ff.Binaries(ffmpeg="/usr/bin/ffmpeg", ffprobe="/usr/bin/ffprobe")


def library_of(count: int = 14) -> Library:
    """A library with enough varied material for several posts."""
    assets = []
    for index in range(count):
        assets.append(
            make_asset(
                f"shot-{index}.jpg",
                base=(240 - index * 3, 230 - index * 3, 214 - index * 2),
                kind="video" if index % 4 == 0 else "image",
                duration=9.0,
                folder=f"/lib/set{index}",
                mtime=1000.0 + index * 9000,
                seed=index,
            )
        )
    return Library(assets=assets)


# -- config ---------------------------------------------------------------

def test_config_defaults_are_usable():
    """A project with no config file still has workable settings."""
    config = Config()
    assert config.preset in presets.names()
    assert config.width == 1080 and config.height == 1920
    assert config.duration > 0


def test_config_roundtrips(tmp_path):
    """Settings survive a save and load."""
    config = Config(roots=["/lib"], handle="@me", duration=12.0, cooldown_days=7)
    config.save(tmp_path)
    restored = Config.load(tmp_path)
    assert restored.roots == ["/lib"]
    assert restored.handle == "@me"
    assert restored.duration == 12.0


def test_config_ignores_unknown_keys(tmp_path):
    """A config from a newer version does not break an older one."""
    from mbtok.util import write_json

    write_json(tmp_path / ".mbtok" / "config.json", {"preset": "old-money", "future_key": 1})
    assert Config.load(tmp_path).preset == "old-money"


def test_output_path_resolution(tmp_path):
    """A relative output folder resolves against the project."""
    config = Config(output_dir="renders")
    assert config.resolved_output(tmp_path) == tmp_path / "renders"
    assert Config(output_dir="/abs/out").resolved_output(tmp_path) == Path("/abs/out")


# -- music and grid -------------------------------------------------------

def test_explicit_music_must_exist(tmp_path):
    """A named track that is missing is reported, not silently ignored."""
    with pytest.raises(pipeline.PipelineError):
        pipeline.pick_music(Config(), tmp_path / "nope.mp3", seed=0)


def test_music_is_chosen_from_the_folder(tmp_path):
    """With a music folder set, a track is picked deterministically."""
    folder = tmp_path / "music"
    folder.mkdir()
    for name in ("a.mp3", "b.mp3", "c.wav"):
        (folder / name).write_bytes(b"x")
    config = Config(music_dir=str(folder))
    first = pipeline.pick_music(config, None, seed=5)
    assert first is not None and first.parent == folder
    assert pipeline.pick_music(config, None, seed=5) == first


def test_no_music_configured_is_allowed():
    """A silent render is a valid outcome, not an error."""
    assert pipeline.pick_music(Config(), None, seed=0) is None


def test_grid_without_music_uses_the_given_tempo():
    """With no track, the requested BPM drives the cut grid."""
    grid = pipeline.beat_grid(None, 128.0, BINARIES, 10.0)
    assert grid.bpm == pytest.approx(128.0)
    assert grid.source == "given"


# -- planning -------------------------------------------------------------

def test_week_plan_cycles_through_moods():
    """A batch rotates the requested moods and names files uniquely."""
    requests = pipeline.plan_week(
        Config(), ["clean-girl", "tokyo-night"], count=5, duration=9.0, prefix="wk"
    )
    assert [r.preset_key for r in requests] == [
        "clean-girl", "tokyo-night", "clean-girl", "tokyo-night", "clean-girl"
    ]
    assert len({r.slug for r in requests}) == 5
    assert all(r.slug.startswith("wk-") for r in requests)


def test_week_plan_falls_back_to_the_default_mood():
    """With no moods named, the configured default is used."""
    requests = pipeline.plan_week(Config(preset="old-money"), [], count=2, duration=8.0)
    assert {r.preset_key for r in requests} == {"old-money"}


def test_seed_is_stable_per_slug():
    """The same slug always renders the same board."""
    assert pipeline._seed_from("post-a") == pipeline._seed_from("post-a")
    assert pipeline._seed_from("post-a") != pipeline._seed_from("post-b")


def test_metadata_states_the_pipeline():
    """Container metadata records that no model generated any frame."""
    metadata = pipeline._metadata(presets.get("clean-girl"), "slug")
    assert "generative AI" in metadata["comment"]
    assert metadata["encoder"] == "mbtok"


# -- dry run --------------------------------------------------------------

def test_dry_run_builds_a_command_without_touching_disk(tmp_path):
    """A dry run produces a runnable command and writes no video or ledger."""
    config = Config(output_dir=str(tmp_path / "out"), music_dir="")
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=8.0,
                                   bpm=104.0, slug="dry", dry_run=True)
    result = pipeline.make(library_of(), config, request, tmp_path, binaries=BINARIES)
    assert result.skipped
    assert "-filter_complex" in result.plan.command
    assert not (tmp_path / "out" / "dry.mp4").exists()
    assert Ledger.load(tmp_path).posts == []


def test_cooldown_excludes_previously_used_assets(tmp_path):
    """Assets recorded in the ledger do not come back inside the window."""
    library = library_of()
    used = [asset.path for asset in library.assets[:6]]
    ledger = Ledger(posts=[Post(slug="old", preset="clean-girl",
                                created_at=__import__("time").time(), assets=used)])
    ledger.save(tmp_path)

    config = Config(output_dir=str(tmp_path / "out"), cooldown_days=21)
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=6.0,
                                   bpm=104.0, shots=4, slug="new", dry_run=True)
    result = pipeline.make(library, config, request, tmp_path, binaries=BINARIES)
    assert set(result.bundle.board.assets).isdisjoint(used)


def test_exhausted_library_raises_a_helpful_error(tmp_path):
    """When everything is resting, the error says how to proceed."""
    library = library_of(6)
    ledger = Ledger(posts=[Post(slug="old", preset="clean-girl",
                                created_at=__import__("time").time(),
                                assets=[a.path for a in library.assets])])
    ledger.save(tmp_path)
    config = Config(output_dir=str(tmp_path / "out"), cooldown_days=21)
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=6.0, bpm=104.0)
    with pytest.raises(pipeline.PipelineError, match="cooldown"):
        pipeline.make(library, config, request, tmp_path, binaries=BINARIES)


def test_unknown_preset_is_rejected(tmp_path):
    """A mistyped mood names the valid options."""
    with pytest.raises(KeyError, match="Available"):
        pipeline.make(
            library_of(), Config(), pipeline.MakeRequest(preset_key="nope"),
            tmp_path, binaries=BINARIES,
        )


def test_no_text_suppresses_overlays(tmp_path):
    """The no-text flag removes both the hook and the sign-off."""
    config = Config(output_dir=str(tmp_path / "out"))
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=8.0, bpm=104.0,
                                   slug="silent", no_text=True, dry_run=True)
    result = pipeline.make(library_of(), config, request, tmp_path, binaries=BINARIES)
    assert result.bundle.board.hook == ""
    assert "drawtext" not in result.plan.filter_complex


def test_handle_overrides_the_signoff(tmp_path):
    """A configured handle replaces the preset's own sign-off."""
    config = Config(output_dir=str(tmp_path / "out"), handle="@studio")
    request = pipeline.MakeRequest(preset_key="clean-girl", duration=8.0,
                                   bpm=104.0, slug="h", dry_run=True)
    result = pipeline.make(library_of(), config, request, tmp_path, binaries=BINARIES)
    assert result.bundle.board.signoff == "@studio"


# -- reporting ------------------------------------------------------------

def bundle_for(tmp_path) -> report.PostBundle:
    """A finished bundle, without rendering anything."""
    preset = presets.get("clean-girl")
    grid = fixed_grid(104.0, duration=60.0)
    selection = [
        make_scored(make_asset(f"s{i}.jpg", folder=f"/lib/{i}", seed=i)) for i in range(5)
    ]
    board = storyboard.build(selection, preset, grid, 9.0, seed=1, hook="hi", signoff="bye")
    return report.PostBundle(
        slug="demo", preset=preset, board=board,
        copy=captions.write(preset, "demo"),
        output=tmp_path / "demo.mp4",
        selection_report=SelectionReport(considered=20, eligible=15, chosen=5, mean_score=0.7),
        music=tmp_path / "track.mp3",
    )


def test_provenance_lists_every_source(tmp_path):
    """Each contributing file appears once, with when it was on screen."""
    record = report.provenance(bundle_for(tmp_path))
    assert record["generative_ai_used"] is False
    assert record["synthetic_media"] is False
    assert len(record["sources"]) == 5
    for entry in record["sources"]:
        assert entry["used_at"]
        assert entry["source_note"]


def test_provenance_merges_repeated_assets(tmp_path):
    """An asset used twice is one entry with two timestamps."""
    bundle = bundle_for(tmp_path)
    bundle.board.shots[1].asset = bundle.board.shots[0].asset
    record = report.provenance(bundle)
    assert len(record["sources"]) == 4
    repeated = next(e for e in record["sources"] if len(e["used_at"]) == 2)
    assert repeated["used_at"][0]["start"] < repeated["used_at"][1]["start"]


def test_sidecars_are_written(tmp_path):
    """A render leaves a caption, a full record and a provenance file."""
    paths = report.write_bundle(bundle_for(tmp_path))
    assert set(paths) == {"caption", "sidecar", "provenance"}
    for path in paths.values():
        assert path.exists() and path.stat().st_size > 0
    data = json.loads(paths["sidecar"].read_text(encoding="utf-8"))
    assert data["slug"] == "demo"
    assert data["storyboard"]["shots"]
    assert data["provenance"]["generative_ai_used"] is False


def test_posting_plan_covers_every_post(tmp_path):
    """The plan lists each post with its caption and hook."""
    bundles = [bundle_for(tmp_path)]
    text = report.posting_plan(bundles)
    assert bundles[0].copy.hook in text
    assert bundles[0].copy.caption in text
    assert "synthetic-media policy" in text
    for tag in bundles[0].copy.hashtags:
        assert tag in text


def test_empty_plan_is_still_valid():
    """A batch that produced nothing writes a plan that says so."""
    assert "Nothing was rendered" in report.posting_plan([])
