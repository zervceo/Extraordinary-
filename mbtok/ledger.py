"""Usage ledger: remembers which assets went into which post, and when.

Reposting the same clip every few days is the fastest way to get a series
quietly suppressed as repetitive, and it is also just boring. The ledger gives
the curator a cooldown window so a shot that shipped on Monday will not come
back until it has rested.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from .util import read_json, state_dir, write_json

#: Days an asset rests before the curator will consider it again.
DEFAULT_COOLDOWN_DAYS = 21

SECONDS_PER_DAY = 86400.0


@dataclass
class Post:
    """One rendered video and the assets that went into it."""

    slug: str
    preset: str
    created_at: float
    assets: list[str] = field(default_factory=list)
    output: str = ""
    duration: float = 0.0

    def to_dict(self) -> dict:
        """JSON representation for the ledger file."""
        return {
            "slug": self.slug,
            "preset": self.preset,
            "created_at": self.created_at,
            "assets": self.assets,
            "output": self.output,
            "duration": round(self.duration, 3),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Post":
        """Rebuild a post record from :meth:`to_dict` output."""
        return cls(
            slug=data.get("slug", ""),
            preset=data.get("preset", ""),
            created_at=float(data.get("created_at", 0.0)),
            assets=list(data.get("assets", [])),
            output=data.get("output", ""),
            duration=float(data.get("duration", 0.0)),
        )


@dataclass
class Ledger:
    """Every post mbtok has rendered for this project."""

    posts: list[Post] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | str = ".") -> "Ledger":
        """Read ``.mbtok/usage.json``, returning an empty ledger when absent."""
        data = read_json(state_dir(root) / "usage.json", default={})
        if not isinstance(data, dict):
            return cls()
        return cls(posts=[Post.from_dict(entry) for entry in data.get("posts", [])])

    def save(self, root: Path | str = ".") -> Path:
        """Write the ledger and return its path."""
        path = state_dir(root, create=True) / "usage.json"
        write_json(path, {"posts": [post.to_dict() for post in self.posts]})
        return path

    def record(self, post: Post) -> None:
        """Append *post*, replacing any earlier record with the same slug."""
        self.posts = [existing for existing in self.posts if existing.slug != post.slug]
        self.posts.append(post)

    def last_used(self, asset_path: str) -> float | None:
        """Timestamp of the most recent post containing *asset_path*."""
        stamps = [post.created_at for post in self.posts if asset_path in post.assets]
        return max(stamps) if stamps else None

    def usage_counts(self) -> dict[str, int]:
        """How many posts each asset has appeared in."""
        counts: dict[str, int] = {}
        for post in self.posts:
            for asset in post.assets:
                counts[asset] = counts.get(asset, 0) + 1
        return counts

    def cooling(self, cooldown_days: float = DEFAULT_COOLDOWN_DAYS, now: float | None = None) -> set[str]:
        """Assets still inside their cooldown window and therefore off-limits."""
        if cooldown_days <= 0:
            return set()
        moment = time.time() if now is None else now
        cutoff = moment - cooldown_days * SECONDS_PER_DAY
        return {
            asset
            for post in self.posts
            if post.created_at >= cutoff
            for asset in post.assets
        }

    def recent_hooks(self, preset: str | None = None, limit: int = 20) -> list[str]:
        """Slugs of recent posts, used to avoid repeating the same hook line."""
        posts = sorted(self.posts, key=lambda post: post.created_at, reverse=True)
        if preset:
            posts = [post for post in posts if post.preset == preset]
        return [post.slug for post in posts[:limit]]

    def preset_counts(self) -> dict[str, int]:
        """How many posts each preset has produced."""
        counts: dict[str, int] = {}
        for post in self.posts:
            counts[post.preset] = counts.get(post.preset, 0) + 1
        return counts

    def summary(self, now: float | None = None) -> dict:
        """Headline numbers for the ``status`` command."""
        moment = time.time() if now is None else now
        week = moment - 7 * SECONDS_PER_DAY
        return {
            "posts": len(self.posts),
            "posts_last_7_days": sum(1 for post in self.posts if post.created_at >= week),
            "assets_used": len(self.usage_counts()),
            "by_preset": self.preset_counts(),
        }
