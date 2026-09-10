"""Write the caption, hook and hashtag set that ship alongside each video.

The video is only half a post. What is written here is chosen from each
preset's own voice, seeded so a given slug always produces the same copy, and
rotated so a batch of seven posts does not open with the same line seven times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .presets import Preset
from .util import dedupe, rng

#: A mixed-reach tag set beats a pile of huge tags. Broad tags provide the
#: ceiling, niche tags find the actual audience, and micro tags are where a
#: small account can realistically rank.
BROAD_COUNT = 2
NICHE_COUNT = 3
MICRO_COUNT = 2

#: Windows that consistently show up as high-activity for lifestyle content,
#: expressed in the poster's own local time.
POSTING_WINDOWS = (
    ("Tue-Thu", "06:30-08:30", "morning scroll, before work"),
    ("Mon-Fri", "11:30-13:00", "lunch break"),
    ("Daily", "18:00-20:00", "the main evening window"),
    ("Fri-Sun", "20:30-23:00", "weekend wind-down"),
)


@dataclass
class PostCopy:
    """Everything textual that goes with one video."""

    hook: str
    caption: str
    cta: str
    hashtags: list[str] = field(default_factory=list)
    signoff: str = ""
    posting_window: str = ""

    @property
    def full_caption(self) -> str:
        """Caption, call to action and hashtags, ready to paste."""
        body = self.caption.strip()
        if self.cta and self.cta.strip() not in body:
            body = f"{body}\n\n{self.cta.strip()}"
        tags = " ".join(self.hashtags)
        return f"{body}\n\n{tags}".strip()

    def to_dict(self) -> dict:
        """JSON representation for the sidecar report."""
        return {
            "hook": self.hook,
            "caption": self.caption,
            "cta": self.cta,
            "hashtags": self.hashtags,
            "signoff": self.signoff,
            "posting_window": self.posting_window,
            "full_caption": self.full_caption,
        }


def _pick_rotating(
    options: Sequence[str],
    generator,
    avoid: Sequence[str] = (),
    fallback: str = "",
) -> str:
    """Choose from *options*, preferring something not in *avoid*.

    When every option has been used recently the avoid list is ignored rather
    than returning nothing, because a repeated line beats a missing one.
    """
    if not options:
        return fallback
    used = {value.strip().lower() for value in avoid}
    unused = [option for option in options if option.strip().lower() not in used]
    return generator.choice(unused or list(options))


def build_hashtags(preset: Preset, generator) -> list[str]:
    """Assemble a mixed-reach hashtag set for *preset*."""
    copy = preset.copy
    chosen: list[str] = []
    for pool, count in (
        (copy.hashtags_broad, BROAD_COUNT),
        (copy.hashtags_niche, NICHE_COUNT),
        (copy.hashtags_micro, MICRO_COUNT),
    ):
        available = list(pool)
        generator.shuffle(available)
        chosen.extend(available[:count])
    return dedupe(tag if tag.startswith("#") else f"#{tag}" for tag in chosen if tag)


def suggest_window(generator) -> str:
    """Pick one posting window, described in plain language."""
    days, hours, note = generator.choice(POSTING_WINDOWS)
    return f"{days} {hours} local ({note})"


def write(
    preset: Preset,
    slug: str,
    used_hooks: Sequence[str] = (),
    used_captions: Sequence[str] = (),
) -> PostCopy:
    """Produce the copy for one post.

    Seeded on the preset and slug, so re-rendering the same board gives the
    same words and a caption the user has already edited is not silently
    replaced with a different one.
    """
    generator = rng("copy", preset.key, slug)
    copy = preset.copy

    hook = _pick_rotating(copy.hooks, generator, used_hooks, fallback=preset.title)
    caption = _pick_rotating(copy.captions, generator, used_captions, fallback=preset.blurb)
    cta = generator.choice(list(copy.ctas)) if copy.ctas else ""

    return PostCopy(
        hook=hook,
        caption=caption,
        cta=cta,
        hashtags=build_hashtags(preset, generator),
        signoff=copy.signoff,
        posting_window=suggest_window(generator),
    )


def write_batch(preset: Preset, slugs: Sequence[str]) -> list[PostCopy]:
    """Copy for a whole batch, rotating so no two posts open the same way."""
    results: list[PostCopy] = []
    used_hooks: list[str] = []
    used_captions: list[str] = []
    for slug in slugs:
        item = write(preset, slug, used_hooks=used_hooks, used_captions=used_captions)
        results.append(item)
        used_hooks.append(item.hook)
        used_captions.append(item.caption)
    return results
