"""Usage tracking and the repeat cooldown."""

from __future__ import annotations

import time

import pytest

from mbtok.ledger import SECONDS_PER_DAY, Ledger, Post

NOW = 1_700_000_000.0


def post(slug: str, days_ago: float, assets: list[str], preset: str = "clean-girl") -> Post:
    """A post recorded *days_ago* days before the fixed test clock."""
    return Post(
        slug=slug,
        preset=preset,
        created_at=NOW - days_ago * SECONDS_PER_DAY,
        assets=assets,
        duration=10.0,
    )


def test_recent_assets_are_cooling():
    """Assets used inside the window are off-limits."""
    ledger = Ledger(posts=[post("a", 3, ["/x/1.jpg", "/x/2.jpg"])])
    assert ledger.cooling(21, now=NOW) == {"/x/1.jpg", "/x/2.jpg"}


def test_old_assets_are_released():
    """Assets past the window become available again."""
    ledger = Ledger(posts=[post("a", 40, ["/x/1.jpg"])])
    assert ledger.cooling(21, now=NOW) == set()


def test_cooldown_boundary_is_inclusive():
    """An asset used exactly at the window edge is still resting."""
    ledger = Ledger(posts=[post("a", 21, ["/x/1.jpg"])])
    assert "/x/1.jpg" in ledger.cooling(21.001, now=NOW)


def test_zero_cooldown_blocks_nothing():
    """Turning the cooldown off frees the whole library."""
    ledger = Ledger(posts=[post("a", 0, ["/x/1.jpg"])])
    assert ledger.cooling(0, now=NOW) == set()


def test_recording_replaces_a_same_slug_post():
    """Re-rendering a post updates its record instead of duplicating it."""
    ledger = Ledger()
    ledger.record(post("daily", 1, ["/x/1.jpg"]))
    ledger.record(post("daily", 0, ["/x/2.jpg"]))
    assert len(ledger.posts) == 1
    assert ledger.posts[0].assets == ["/x/2.jpg"]


def test_usage_counts_across_posts():
    """Repeat use of an asset is counted."""
    ledger = Ledger(posts=[post("a", 30, ["/x/1.jpg"]), post("b", 60, ["/x/1.jpg", "/x/2.jpg"])])
    assert ledger.usage_counts() == {"/x/1.jpg": 2, "/x/2.jpg": 1}


def test_last_used_returns_the_most_recent():
    """The newest use wins when an asset appears in several posts."""
    ledger = Ledger(posts=[post("old", 30, ["/x/1.jpg"]), post("new", 2, ["/x/1.jpg"])])
    assert ledger.last_used("/x/1.jpg") == pytest.approx(NOW - 2 * SECONDS_PER_DAY)
    assert ledger.last_used("/x/absent.jpg") is None


def test_summary_counts_the_last_week():
    """The summary separates recent activity from the total."""
    ledger = Ledger(posts=[post("a", 1, ["/x/1.jpg"]), post("b", 20, ["/x/2.jpg"])])
    summary = ledger.summary(now=NOW)
    assert summary["posts"] == 2
    assert summary["posts_last_7_days"] == 1
    assert summary["assets_used"] == 2


def test_preset_counts():
    """Posts are tallied per mood."""
    ledger = Ledger(
        posts=[
            post("a", 1, ["/x/1.jpg"], preset="clean-girl"),
            post("b", 2, ["/x/2.jpg"], preset="clean-girl"),
            post("c", 3, ["/x/3.jpg"], preset="tokyo-night"),
        ]
    )
    assert ledger.preset_counts() == {"clean-girl": 2, "tokyo-night": 1}


def test_recent_hooks_filters_by_preset():
    """Hook rotation only looks at the mood being rendered."""
    ledger = Ledger(
        posts=[
            post("cg-1", 1, [], preset="clean-girl"),
            post("tn-1", 2, [], preset="tokyo-night"),
        ]
    )
    assert ledger.recent_hooks("clean-girl") == ["cg-1"]


def test_ledger_roundtrips_on_disk(tmp_path):
    """Saving and loading preserves every post."""
    ledger = Ledger(posts=[post("a", 1, ["/x/1.jpg", "/x/2.jpg"])])
    ledger.save(tmp_path)
    restored = Ledger.load(tmp_path)
    assert len(restored.posts) == 1
    assert restored.posts[0].assets == ["/x/1.jpg", "/x/2.jpg"]
    assert restored.posts[0].slug == "a"


def test_missing_ledger_loads_empty(tmp_path):
    """A project with no history starts clean rather than failing."""
    assert Ledger.load(tmp_path).posts == []


def test_corrupt_ledger_does_not_crash(tmp_path):
    """A damaged ledger file degrades to an empty history."""
    state = tmp_path / ".mbtok"
    state.mkdir()
    (state / "usage.json").write_text("{not json", encoding="utf-8")
    assert Ledger.load(tmp_path).posts == []


def test_default_clock_is_now():
    """Omitting the clock uses the real time."""
    ledger = Ledger(posts=[Post(slug="a", preset="p", created_at=time.time(), assets=["/x/1"])])
    assert ledger.cooling(7) == {"/x/1"}
