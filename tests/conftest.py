"""Shared fixtures: synthetic assets that need no files on disk."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mbtok.color import analyze  # noqa: E402
from mbtok.curate import ScoredAsset  # noqa: E402
from mbtok.library import Asset  # noqa: E402


def pixels_around(base: tuple[int, int, int], count: int = 80, spread: int = 26, seed: int = 0):
    """A cloud of pixels scattered around *base*, for building test palettes."""
    generator = random.Random(seed)
    return [
        tuple(max(0, min(255, channel + generator.randint(-spread, spread))) for channel in base)
        for _ in range(count)
    ]


def make_asset(
    name: str,
    base: tuple[int, int, int] = (230, 220, 205),
    kind: str = "image",
    folder: str = "/lib/a",
    width: int = 1080,
    height: int = 1920,
    duration: float = 0.0,
    quality: float = 0.8,
    mtime: float = 1000.0,
    seed: int = 0,
) -> Asset:
    """Build an :class:`Asset` with a real analysed palette."""
    return Asset(
        path=f"{folder}/{name}",
        kind=kind,
        fingerprint=name,
        width=width,
        height=height,
        duration=duration if kind == "video" else 0.0,
        quality=quality,
        palette=analyze(pixels_around(base, seed=seed)),
        mtime=mtime,
    )


def make_scored(asset: Asset, score: float = 0.8) -> ScoredAsset:
    """Wrap an asset with a fixed score."""
    return ScoredAsset(asset=asset, score=score)


@pytest.fixture
def cream_library() -> list[Asset]:
    """Twelve varied warm-neutral assets, four of them footage."""
    assets = []
    for index in range(8):
        assets.append(
            make_asset(
                f"linen-{index}.jpg",
                base=(238 - index * 4, 228 - index * 3, 212 - index * 2),
                folder=f"/lib/shoot{index % 4}",
                mtime=1000.0 + index * 5000,
                seed=index,
            )
        )
    for index in range(4):
        assets.append(
            make_asset(
                f"curtain-{index}.mp4",
                base=(232 - index * 6, 222 - index * 5, 206 - index * 4),
                kind="video",
                folder=f"/lib/motion{index}",
                duration=8.0,
                mtime=50000.0 + index * 5000,
                seed=100 + index,
            )
        )
    return assets


@pytest.fixture
def mixed_library(cream_library) -> list[Asset]:
    """The cream set plus clearly off-mood dark and neon assets."""
    extra = [
        make_asset(f"night-{i}.jpg", base=(18, 26, 54), folder="/lib/night",
                   mtime=200000.0 + i * 4000, seed=200 + i)
        for i in range(6)
    ] + [
        make_asset(f"neon-{i}.jpg", base=(205, 40, 120), folder="/lib/neon",
                   mtime=300000.0 + i * 4000, seed=300 + i)
        for i in range(6)
    ]
    return cream_library + extra
