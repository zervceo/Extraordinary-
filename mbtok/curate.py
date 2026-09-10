"""Choose which assets go into a board, and in what order.

Two problems live here. Picking the *right* shots is a scoring problem: how
well does this frame sit inside the mood. Picking a *good set* is a diversity
problem: eight near-identical beige flatlays all score beautifully and make a
terrible video. The selector solves the second by penalising each candidate
against what has already been chosen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .color import delta_e
from .library import Asset
from .presets import Preset
from .util import clamp

#: Similarity below this is treated as "different enough", and costs nothing.
SIMILARITY_FLOOR = 0.45

#: The most a diversity penalty can ever subtract from a match score.
MAX_DIVERSITY_PENALTY = 0.60

#: Above this similarity two assets are effectively the same shot - a duplicate
#: file, or two frames from the same burst. The penalty system alone cannot be
#: trusted here: when a library is small, a heavily penalised duplicate can
#: still outscore everything else, and two identical clips back to back is the
#: one mistake a viewer always notices.
NEAR_DUPLICATE = 0.90



@dataclass
class ScoredAsset:
    """An asset with its match score and the breakdown behind it."""

    asset: Asset
    score: float
    parts: dict[str, float] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """Convenience passthrough to the asset path."""
        return self.asset.path

    @property
    def energy(self) -> float:
        """How much a shot grabs attention, in 0-1.

        Used for ordering rather than selection: a board should open loud,
        breathe in the middle and close on something memorable.
        """
        palette = self.asset.palette
        centred_brightness = 1.0 - min(1.0, abs(palette.brightness - 0.5) * 2.0)
        return clamp(
            palette.colorfulness * 0.40
            + palette.contrast * 0.38
            + centred_brightness * 0.12
            + (0.10 if self.asset.kind == "video" else 0.0),
            0.0,
            1.0,
        )


def _closeness(value: float, target: float, tolerance: float) -> float:
    """1.0 when *value* equals *target*, falling to 0 at *tolerance* away."""
    if tolerance <= 0:
        return 1.0 if value == target else 0.0
    return clamp(1.0 - abs(value - target) / tolerance, 0.0, 1.0)


def keyword_bonus(asset: Asset, preset: Preset) -> float:
    """Reward filenames and folders that name the mood.

    Stock libraries carry descriptive filenames ("linen-bedding-morning-04.mp4"),
    which is free signal about content that colour alone cannot see.
    """
    if not preset.keywords:
        return 0.0
    haystack = asset.path.lower().replace("_", " ").replace("-", " ")
    hits = sum(1 for keyword in preset.keywords if keyword in haystack)
    return min(1.0, hits / 2.0)


def score_asset(asset: Asset, preset: Preset) -> ScoredAsset:
    """Score one asset against a preset, returning the weighted breakdown."""
    palette = asset.palette

    # A delta_e around 40 means "different family of colours" entirely. The
    # exponent bends the curve so a near-match still scores well while a
    # halfway match drops off sharply, which is what separates a beige frame
    # from a merely dark one.
    distance = _palette_distance(asset, preset)
    parts = {
        "palette": clamp(1.0 - distance / 40.0, 0.0, 1.0) ** 1.3,
        "brightness": _closeness(palette.brightness, preset.brightness, 0.42),
        "saturation": _closeness(palette.saturation, preset.saturation, 0.45),
        "contrast": _closeness(palette.contrast, preset.contrast, 0.5),
        "warmth": _closeness(palette.warmth, preset.warmth, 0.6),
        "hue_spread": _closeness(palette.hue_spread, preset.hue_spread, 0.6),
        "quality": asset.quality,
        "vertical": 1.0 if asset.is_vertical else (0.55 if asset.aspect <= 1.05 else 0.3),
    }

    total_weight = 0.0
    total = 0.0
    for name, value in parts.items():
        weight = preset.weight(name)
        total += value * weight
        total_weight += weight

    base = total / total_weight if total_weight else 0.0
    bonus = keyword_bonus(asset, preset) * 0.06
    return ScoredAsset(asset=asset, score=clamp(base + bonus, 0.0, 1.0), parts=parts)


def _palette_distance(asset: Asset, preset: Preset) -> float:
    """Weighted perceptual distance from an asset's palette to the preset's."""
    swatches = asset.palette.swatches
    targets = preset.palette
    if not swatches or not targets:
        return 40.0
    total_weight = sum(swatch.weight for swatch in swatches) or 1.0
    total = 0.0
    for swatch in swatches:
        total += min(delta_e(swatch.rgb, target) for target in targets) * swatch.weight
    return total / total_weight


def similarity(first: Asset, second: Asset) -> float:
    """How interchangeable two assets are, in 0-1.

    Colour is the main signal, reinforced by two structural hints: files that
    live in the same folder are usually the same shoot, and files saved within
    a minute of each other are usually the same burst.
    """
    first_swatches = first.palette.swatches[:3]
    second_swatches = second.palette.swatches[:3]
    if not first_swatches or not second_swatches:
        color_similarity = 0.0
    else:
        distances = [
            min(delta_e(a.rgb, b.rgb) for b in second_swatches) for a in first_swatches
        ]
        mean_distance = sum(distances) / len(distances)
        color_similarity = clamp(1.0 - mean_distance / 30.0, 0.0, 1.0)

    same_folder = 0.18 if first.folder == second.folder else 0.0
    burst = 0.22 if abs(first.mtime - second.mtime) < 60.0 and first.mtime > 0 else 0.0
    stem_overlap = 0.15 if _shared_stem(first, second) else 0.0
    return clamp(color_similarity * 0.7 + same_folder + burst + stem_overlap, 0.0, 1.0)


def _shared_stem(first: Asset, second: Asset) -> bool:
    """True when two filenames share a long prefix, as numbered exports do."""
    a, b = Path(first.path).stem.lower(), Path(second.path).stem.lower()
    if a == b:
        return True
    shortest = min(len(a), len(b))
    if shortest < 8:
        return False
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= max(8, int(shortest * 0.75))


@dataclass
class SelectionReport:
    """Why a selection came out the way it did, for the ``boards`` command."""

    considered: int = 0
    eligible: int = 0
    cooled_out: int = 0
    chosen: int = 0
    mean_score: float = 0.0
    warnings: list[str] = field(default_factory=list)


def eligible_assets(
    assets: Sequence[Asset],
    preset: Preset,
    min_quality: float = 0.35,
    min_pixels: int = 500_000,
    exclude: Sequence[str] = (),
    allow_video: bool = True,
) -> list[Asset]:
    """Filter the library down to assets worth scoring at all."""
    blocked = set(exclude)
    kept: list[Asset] = []
    for asset in assets:
        if not asset.usable or asset.path in blocked:
            continue
        if asset.kind == "video" and not allow_video:
            continue
        if asset.kind == "video" and asset.duration < 1.0:
            continue
        if asset.width * asset.height < min_pixels:
            continue
        if asset.quality < min_quality:
            continue
        kept.append(asset)
    return kept


def select(
    assets: Sequence[Asset],
    preset: Preset,
    count: int,
    exclude: Sequence[str] = (),
    min_quality: float = 0.35,
    diversity: float = 0.55,
    allow_video: bool = True,
    video_ratio: float | None = None,
) -> tuple[list[ScoredAsset], SelectionReport]:
    """Pick *count* assets that fit *preset* without repeating themselves.

    *diversity* trades match quality against variety: 0 takes the top scores
    straight off the list, 1 will accept a noticeably worse frame to avoid
    another shot of the same thing. The default sits closer to variety because
    a mood board is judged on its range.
    """
    report = SelectionReport(considered=len(assets))
    pool = eligible_assets(
        assets, preset, min_quality=min_quality, exclude=exclude, allow_video=allow_video
    )
    report.eligible = len(pool)
    report.cooled_out = len(set(exclude))

    if not pool:
        report.warnings.append(
            "No assets passed the quality filter. Scan more folders or lower --min-quality."
        )
        return [], report

    scored = sorted(
        (score_asset(asset, preset) for asset in pool),
        key=lambda item: item.score,
        reverse=True,
    )

    target_videos = _target_video_count(count, preset, video_ratio) if allow_video else 0
    chosen = _greedy_diverse(scored, count, diversity, target_videos)

    report.chosen = len(chosen)
    if chosen:
        report.mean_score = sum(item.score for item in chosen) / len(chosen)
    if len(chosen) < count:
        report.warnings.append(
            f"Wanted {count} shots but only {len(chosen)} assets qualified."
        )
    if report.mean_score and report.mean_score < 0.42:
        report.warnings.append(
            f"Average match is only {report.mean_score:.2f}. This library may not "
            f"suit the '{preset.key}' mood."
        )
    return chosen, report


def _target_video_count(count: int, preset: Preset, video_ratio: float | None) -> int:
    """How many of the chosen shots should ideally be motion, not stills."""
    ratio = preset.prefers_video_ratio if video_ratio is None else video_ratio
    return int(round(count * clamp(ratio, 0.0, 1.0)))


def _greedy_diverse(
    scored: Sequence[ScoredAsset],
    count: int,
    diversity: float,
    target_videos: int,
) -> list[ScoredAsset]:
    """Greedily take the best remaining candidate after a similarity penalty.

    A soft quota nudges the result toward the preset's preferred mix of stills
    and motion without ever letting the quota override a much better frame.
    """
    chosen: list[ScoredAsset] = []
    remaining = list(scored)

    while remaining and len(chosen) < count:
        videos_chosen = sum(1 for item in chosen if item.asset.kind == "video")
        slots_left = count - len(chosen)
        videos_needed = max(0, target_videos - videos_chosen)
        video_pressure = clamp(videos_needed / slots_left, 0.0, 1.0) if slots_left else 0.0

        best_index = -1
        best_value = -math.inf
        fallback_index = 0
        fallback_value = -math.inf

        for index, candidate in enumerate(remaining):
            worst = 0.0
            if chosen:
                worst = max(similarity(candidate.asset, item.asset) for item in chosen)
            nudge = 0.0
            if candidate.asset.kind == "video":
                nudge = 0.12 * video_pressure
            elif videos_needed >= slots_left:
                nudge = -0.10
            value = candidate.score - _diversity_penalty(worst, diversity) + nudge

            if value > fallback_value:
                fallback_value, fallback_index = value, index
            if worst < NEAR_DUPLICATE and value > best_value:
                best_value, best_index = value, index

        # Only fall back to a near-duplicate when the library has nothing else
        # left to offer, which is better than returning a short board.
        chosen.append(remaining.pop(best_index if best_index >= 0 else fallback_index))

    return chosen


def _diversity_penalty(worst_similarity: float, diversity: float) -> float:
    """Convert a similarity score into a bounded selection penalty.

    Two properties matter. Shots that are already distinct are not penalised at
    all, so the curator is never pushed away from a good frame for no reason.
    And the penalty is capped well below the range of the score itself, so
    wanting variety can reorder near-equals but can never drag an off-mood
    frame ahead of an on-mood one.
    """
    excess = max(0.0, worst_similarity - SIMILARITY_FLOOR) / (1.0 - SIMILARITY_FLOOR)
    return clamp(diversity, 0.0, 1.0) * excess * MAX_DIVERSITY_PENALTY


def order_shots(selection: Sequence[ScoredAsset]) -> list[ScoredAsset]:
    """Sequence the chosen shots so the board has a shape.

    The strongest frame opens (the first half-second decides whether anyone
    watches), the second strongest closes, and the middle alternates between
    high- and low-energy shots so the edit breathes instead of shouting.
    """
    items = list(selection)
    if len(items) <= 2:
        return items

    ranked = sorted(items, key=lambda item: (item.energy, item.score), reverse=True)
    opener = ranked[0]
    closer = ranked[1]
    middle = ranked[2:]

    loud = middle[: len(middle) // 2]
    quiet = middle[len(middle) // 2 :]
    quiet.reverse()

    woven: list[ScoredAsset] = []
    for index in range(max(len(loud), len(quiet))):
        if index < len(quiet):
            woven.append(quiet[index])
        if index < len(loud):
            woven.append(loud[index])

    return [opener, *woven, closer]


def summarize(selection: Sequence[ScoredAsset]) -> dict:
    """Aggregate stats about a selection for reports and the CLI."""
    if not selection:
        return {"count": 0}
    videos = sum(1 for item in selection if item.asset.kind == "video")
    return {
        "count": len(selection),
        "videos": videos,
        "photos": len(selection) - videos,
        "mean_score": round(sum(item.score for item in selection) / len(selection), 4),
        "min_score": round(min(item.score for item in selection), 4),
        "sources": sorted({item.asset.source for item in selection}),
    }
