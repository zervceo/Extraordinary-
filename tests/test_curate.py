"""Asset scoring, selection and shot ordering."""

from __future__ import annotations

import pytest

from mbtok import curate, presets
from tests.conftest import make_asset


def test_scoring_prefers_the_matching_mood(cream_library, mixed_library):
    """Cream assets outscore navy and neon ones for the clean-girl mood."""
    preset = presets.get("clean-girl")
    cream = [curate.score_asset(a, preset).score for a in cream_library]
    off_mood = [
        curate.score_asset(a, preset).score
        for a in mixed_library
        if a.path not in {c.path for c in cream_library}
    ]
    assert min(cream) > max(off_mood)


def test_scoring_is_mood_specific(mixed_library):
    """The same library ranks differently under a different mood."""
    night = [a for a in mixed_library if "night" in a.path]
    clean_girl = presets.get("clean-girl")
    tokyo = presets.get("tokyo-night")
    for asset in night:
        assert (
            curate.score_asset(asset, tokyo).score
            > curate.score_asset(asset, clean_girl).score
        )


def test_score_breakdown_is_complete():
    """Every scoring term is reported, so the result can be explained."""
    scored = curate.score_asset(make_asset("x.jpg"), presets.get("clean-girl"))
    assert set(scored.parts) == {
        "palette", "brightness", "saturation", "contrast",
        "warmth", "hue_spread", "quality", "vertical",
    }
    assert 0.0 <= scored.score <= 1.0


def test_keyword_bonus_reads_the_filename():
    """A descriptive stock filename counts as evidence of content."""
    preset = presets.get("coastal-linen")
    plain = make_asset("IMG_4821.jpg", folder="/lib/x")
    named = make_asset("beach-ocean-linen-04.jpg", folder="/lib/x")
    assert curate.keyword_bonus(named, preset) > curate.keyword_bonus(plain, preset)


def test_vertical_assets_are_preferred():
    """A portrait frame beats an identical landscape one."""
    preset = presets.get("clean-girl")
    portrait = make_asset("a.jpg", width=1080, height=1920)
    landscape = make_asset("b.jpg", width=1920, height=1080)
    assert (
        curate.score_asset(portrait, preset).score
        > curate.score_asset(landscape, preset).score
    )


def test_similarity_detects_the_same_shot():
    """Two frames from one burst read as near-identical."""
    first = make_asset("shoot-a-001.jpg", folder="/lib/s", mtime=1000.0, seed=1)
    second = make_asset("shoot-a-002.jpg", folder="/lib/s", mtime=1010.0, seed=1)
    assert curate.similarity(first, second) > curate.NEAR_DUPLICATE


def test_similarity_separates_different_shots():
    """Different colours, folders and times read as different shots."""
    first = make_asset("a.jpg", base=(240, 230, 215), folder="/lib/a", mtime=1000.0)
    second = make_asset("b.jpg", base=(20, 30, 60), folder="/lib/b", mtime=900000.0)
    assert curate.similarity(first, second) < 0.4


def test_diversity_penalty_is_bounded():
    """The penalty never exceeds its cap and is zero for distinct assets."""
    assert curate._diversity_penalty(0.0, 1.0) == 0.0
    assert curate._diversity_penalty(curate.SIMILARITY_FLOOR, 1.0) == 0.0
    assert curate._diversity_penalty(1.0, 1.0) == pytest.approx(curate.MAX_DIVERSITY_PENALTY)
    assert curate._diversity_penalty(1.0, 0.5) < curate.MAX_DIVERSITY_PENALTY


def test_selection_stays_on_mood(mixed_library):
    """Wanting variety must not drag off-mood assets into the board."""
    selection, _ = curate.select(mixed_library, presets.get("clean-girl"), 6)
    assert len(selection) == 6
    for item in selection:
        assert "night" not in item.path and "neon" not in item.path


def test_selection_avoids_near_duplicates():
    """Identical clips do not both make the cut when alternatives exist."""
    twins = [
        make_asset("twin-a.jpg", folder="/lib/t", mtime=1000.0, seed=1),
        make_asset("twin-b.jpg", folder="/lib/t", mtime=1001.0, seed=1),
    ]
    others = [
        make_asset(f"other-{i}.jpg", base=(232 - i * 5, 222 - i * 4, 208 - i * 3),
                   folder=f"/lib/o{i}", mtime=90000.0 + i * 9000, seed=40 + i)
        for i in range(6)
    ]
    selection, _ = curate.select(twins + others, presets.get("clean-girl"), 4)
    chosen = {item.asset.name for item in selection}
    assert not {"twin-a.jpg", "twin-b.jpg"} <= chosen


def test_selection_respects_the_exclude_list(cream_library):
    """Assets inside their cooldown are never selected."""
    blocked = [cream_library[0].path, cream_library[1].path]
    selection, report = curate.select(
        cream_library, presets.get("clean-girl"), 5, exclude=blocked
    )
    assert {item.path for item in selection}.isdisjoint(blocked)
    assert report.cooled_out == 2


def test_selection_mixes_stills_and_footage(cream_library):
    """The preset's preferred motion ratio is roughly honoured."""
    selection, _ = curate.select(cream_library, presets.get("clean-girl"), 8)
    videos = sum(1 for item in selection if item.asset.kind == "video")
    assert videos >= 2


def test_selection_can_exclude_video(cream_library):
    """Stills-only selection returns no footage."""
    selection, _ = curate.select(
        cream_library, presets.get("clean-girl"), 5, allow_video=False
    )
    assert all(item.asset.kind == "image" for item in selection)


def test_empty_library_reports_a_reason():
    """An empty result explains itself instead of failing silently."""
    selection, report = curate.select([], presets.get("clean-girl"), 5)
    assert selection == []
    assert report.warnings


def test_low_quality_assets_are_filtered():
    """Assets below the quality floor never reach scoring."""
    poor = [make_asset(f"p{i}.jpg", quality=0.1, folder=f"/lib/{i}") for i in range(5)]
    eligible = curate.eligible_assets(poor, presets.get("clean-girl"), min_quality=0.35)
    assert eligible == []


def test_short_clips_are_filtered():
    """Sub-second footage cannot fill a shot and is skipped."""
    clip = make_asset("blip.mp4", kind="video", duration=0.4)
    assert curate.eligible_assets([clip], presets.get("clean-girl")) == []


def test_ordering_opens_and_closes_on_strong_shots(cream_library):
    """The highest-energy frame opens and the next-highest closes."""
    selection, _ = curate.select(cream_library, presets.get("clean-girl"), 7)
    ordered = curate.order_shots(selection)
    assert len(ordered) == len(selection)
    energies = [item.energy for item in ordered]
    assert energies[0] == max(energies)
    assert energies[-1] >= max(energies[1:-1])


def test_ordering_preserves_every_shot(cream_library):
    """Ordering rearranges without dropping or duplicating anything."""
    selection, _ = curate.select(cream_library, presets.get("clean-girl"), 8)
    ordered = curate.order_shots(selection)
    assert sorted(i.path for i in ordered) == sorted(i.path for i in selection)


def test_ordering_handles_tiny_boards():
    """One or two shots need no rearranging."""
    from tests.conftest import make_scored

    one = [make_scored(make_asset("a.jpg"))]
    assert curate.order_shots(one) == one
    assert len(curate.order_shots(one * 2)) == 2


def test_selection_is_deterministic(mixed_library):
    """The same library and mood always produce the same board."""
    first, _ = curate.select(mixed_library, presets.get("vanilla-girl"), 6)
    second, _ = curate.select(mixed_library, presets.get("vanilla-girl"), 6)
    assert [i.path for i in first] == [i.path for i in second]


def test_summarize_reports_the_mix(cream_library):
    """The summary counts stills, footage and the score range."""
    selection, _ = curate.select(cream_library, presets.get("clean-girl"), 6)
    summary = curate.summarize(selection)
    assert summary["count"] == 6
    assert summary["photos"] + summary["videos"] == 6
    assert summary["min_score"] <= summary["mean_score"]
