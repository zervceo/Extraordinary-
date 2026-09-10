"""Caption, hook and hashtag generation."""

from __future__ import annotations

import pytest

from mbtok import captions, presets

ALL_PRESETS = presets.names()


@pytest.mark.parametrize("key", ALL_PRESETS)
def test_every_preset_produces_complete_copy(key):
    """No mood is missing a hook, caption or hashtag set."""
    copy = captions.write(presets.get(key), "slug-1")
    assert copy.hook
    assert copy.caption
    assert copy.hashtags
    assert copy.posting_window
    assert copy.full_caption.strip()


def test_copy_is_deterministic():
    """The same preset and slug always produce the same words."""
    first = captions.write(presets.get("clean-girl"), "same-slug")
    second = captions.write(presets.get("clean-girl"), "same-slug")
    assert first.to_dict() == second.to_dict()


def test_different_slugs_vary_the_copy():
    """Across many slugs the hook actually changes."""
    hooks = {captions.write(presets.get("coastal-linen"), f"s{i}").hook for i in range(12)}
    assert len(hooks) > 1


@pytest.mark.parametrize("key", ALL_PRESETS)
def test_hashtag_mix_is_balanced(key):
    """Every tag set draws from all three reach tiers without duplicates."""
    preset = presets.get(key)
    copy = captions.write(preset, "slug")
    assert len(copy.hashtags) == len(set(copy.hashtags))
    assert all(tag.startswith("#") for tag in copy.hashtags)
    assert all(" " not in tag for tag in copy.hashtags)
    broad = set(preset.copy.hashtags_broad)
    assert len(broad & set(copy.hashtags)) >= 1
    assert 5 <= len(copy.hashtags) <= 8


def test_batch_rotates_hooks():
    """A batch does not open every post with the same line."""
    preset = presets.get("dark-academia")
    batch = captions.write_batch(preset, [f"post-{i}" for i in range(5)])
    assert len({item.hook for item in batch}) == 5


def test_batch_rotation_survives_running_out_of_options():
    """More posts than hooks still yields copy for every post."""
    preset = presets.get("clean-girl")
    slugs = [f"p{i}" for i in range(len(preset.copy.hooks) + 4)]
    batch = captions.write_batch(preset, slugs)
    assert len(batch) == len(slugs)
    assert all(item.hook for item in batch)


def test_full_caption_contains_body_cta_and_tags():
    """The pasteable caption carries all three parts."""
    copy = captions.write(presets.get("cottagecore"), "slug")
    text = copy.full_caption
    assert copy.caption in text
    assert copy.cta in text
    for tag in copy.hashtags:
        assert tag in text


def test_cta_is_not_duplicated():
    """A call to action already inside the caption is not appended twice."""
    copy = captions.PostCopy(hook="h", caption="Save this for later.", cta="Save this for later.")
    assert copy.full_caption.count("Save this for later.") == 1


def test_posting_window_is_descriptive():
    """The suggested window names days, a time range and a reason."""
    copy = captions.write(presets.get("old-money"), "slug")
    assert "local" in copy.posting_window
    assert ":" in copy.posting_window


def test_hooks_are_not_absurdly_long():
    """Every shipped hook is short enough to read in a first second."""
    for key in ALL_PRESETS:
        for hook in presets.get(key).copy.hooks:
            assert len(hook) <= 60, f"{key}: {hook}"
