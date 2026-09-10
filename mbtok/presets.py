"""Mood presets: the look, pacing, grade and copy voice of each board.

A preset is the whole creative brief in one object. It tells the curator which
images belong in the mood, the renderer how to grade and cut them, and the
caption writer how to talk. Adding a new aesthetic means adding one entry here
and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .color import RGB, hex_to_rgb


@dataclass(frozen=True)
class Grade:
    """Colour grade applied to every shot so mixed sources match.

    ``contrast``, ``brightness``, ``saturation`` and ``gamma`` map onto
    ffmpeg's ``eq`` filter. The three balance triples are red/green/blue lifts
    for shadows, midtones and highlights, matching ``colorbalance``; they are
    what gives each preset its split-toned, film-like cast.
    """

    contrast: float = 1.0
    brightness: float = 0.0
    saturation: float = 1.0
    gamma: float = 1.0
    shadows: tuple[float, float, float] = (0.0, 0.0, 0.0)
    midtones: tuple[float, float, float] = (0.0, 0.0, 0.0)
    highlights: tuple[float, float, float] = (0.0, 0.0, 0.0)
    grain: float = 0.0
    vignette: float = 0.0
    blur_bloom: float = 0.0

    @property
    def is_identity(self) -> bool:
        """True when the grade would not change a single pixel."""
        return (
            self.contrast == 1.0
            and self.brightness == 0.0
            and self.saturation == 1.0
            and self.gamma == 1.0
            and not any(self.shadows)
            and not any(self.midtones)
            and not any(self.highlights)
        )


@dataclass(frozen=True)
class Motion:
    """Ken Burns behaviour for stills.

    ``styles`` names the moves the renderer may cycle through. ``zoom`` is the
    total scale change across a shot, so 0.08 means an 8% push. Keeping this
    low is deliberate: fast zooms read as a template, slow ones read as film.
    """

    styles: tuple[str, ...] = ("push_in", "pull_out", "pan_left", "pan_right", "drift_up")
    zoom: float = 0.08
    drift: float = 0.06


@dataclass(frozen=True)
class Typography:
    """Text overlay styling for the hook and the sign-off."""

    fonts: tuple[str, ...] = ()
    case: str = "lower"  # lower | upper | title | as_is
    size_ratio: float = 0.058  # fraction of the 1080px frame width
    color: str = "#ffffff"
    shadow: str = "#00000080"
    letter_spacing: int = 2
    position: str = "upper"  # upper | center | lower
    box: bool = False
    box_color: str = "#00000059"


@dataclass(frozen=True)
class Pacing:
    """How the edit breathes: shot length and how cuts relate to the beat."""

    beats_per_shot: tuple[int, ...] = (2,)
    min_shot: float = 0.7
    max_shot: float = 2.6
    transition: tuple[str, ...] = ("fade",)
    transition_seconds: float = 0.28
    #: Shots that open and close the video get a little extra room to land.
    anchor_bonus: float = 0.35


@dataclass(frozen=True)
class Copy:
    """The caption voice: hooks, captions and hashtag pools."""

    hooks: tuple[str, ...] = ()
    captions: tuple[str, ...] = ()
    ctas: tuple[str, ...] = ()
    hashtags_broad: tuple[str, ...] = ()
    hashtags_niche: tuple[str, ...] = ()
    hashtags_micro: tuple[str, ...] = ()
    signoff: str = ""


@dataclass(frozen=True)
class Preset:
    """One complete aesthetic: palette, grade, motion, pacing and voice."""

    key: str
    title: str
    blurb: str
    palette_hex: tuple[str, ...]
    brightness: float = 0.5
    saturation: float = 0.4
    contrast: float = 0.45
    warmth: float = 0.0
    hue_spread: float = 0.35
    #: Relative pull of each scoring term. Higher means the curator cares more.
    weights: dict[str, float] = field(default_factory=dict)
    grade: Grade = field(default_factory=Grade)
    motion: Motion = field(default_factory=Motion)
    typography: Typography = field(default_factory=Typography)
    pacing: Pacing = field(default_factory=Pacing)
    copy: Copy = field(default_factory=Copy)
    music: str = ""
    keywords: tuple[str, ...] = ()
    prefers_video_ratio: float = 0.35

    @property
    def palette(self) -> list[RGB]:
        """Target colours as RGB triples."""
        return [hex_to_rgb(value) for value in self.palette_hex]

    def weight(self, name: str, default: float = 1.0) -> float:
        """Look up a scoring weight with a sensible fallback."""
        return float(self.weights.get(name, default))


_DEFAULT_WEIGHTS = {
    "palette": 3.0,
    "brightness": 1.2,
    "saturation": 1.0,
    "contrast": 0.8,
    "warmth": 1.0,
    "hue_spread": 0.9,
    "quality": 1.4,
    "vertical": 0.8,
}


def _weights(**overrides: float) -> dict[str, float]:
    """Merge per-preset weight overrides onto the defaults."""
    merged = dict(_DEFAULT_WEIGHTS)
    merged.update(overrides)
    return merged


PRESETS: dict[str, Preset] = {}


def register(preset: Preset) -> Preset:
    """Add *preset* to the global registry, rejecting duplicate keys."""
    if preset.key in PRESETS:
        raise ValueError(f"duplicate preset key: {preset.key}")
    PRESETS[preset.key] = preset
    return preset


def get(key: str) -> Preset:
    """Fetch a preset by key, with a helpful error listing valid keys."""
    try:
        return PRESETS[key]
    except KeyError:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(f"unknown preset {key!r}. Available: {known}") from None


def names() -> list[str]:
    """All registered preset keys, sorted."""
    return sorted(PRESETS)


# --------------------------------------------------------------------------
# The aesthetics.
#
# Palettes are sampled from the kind of imagery each mood is built on, not
# picked from a colour wheel: they are what the curator matches your library
# against, so they need to describe real photographs.
# --------------------------------------------------------------------------

register(Preset(
    key="clean-girl",
    title="Clean Girl",
    blurb="Milk, linen and morning light. Slicked-back, unfussy, expensive-looking.",
    palette_hex=("#f4efe8", "#e6dccf", "#cbbcab", "#a8927c", "#6f6055", "#2f2a26"),
    brightness=0.68, saturation=0.22, contrast=0.34, warmth=0.12, hue_spread=0.18,
    weights=_weights(palette=3.4, brightness=1.8, saturation=1.6, hue_spread=1.3),
    grade=Grade(
        contrast=1.04, brightness=0.03, saturation=0.88, gamma=1.03,
        shadows=(0.01, 0.005, 0.02), midtones=(0.02, 0.0, -0.01), highlights=(0.03, 0.02, -0.01),
        grain=3.0, vignette=0.18,
    ),
    motion=Motion(styles=("push_in", "drift_up", "pan_right"), zoom=0.07, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="lower", size_ratio=0.060, color="#fffdf9", letter_spacing=4, position="upper",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=0.9, max_shot=2.2,
                  transition=("fade", "dissolve"), transition_seconds=0.32),
    copy=Copy(
        hooks=(
            "romanticising the ordinary",
            "soft life, loud results",
            "the 5am club but make it gentle",
            "things that make me feel put together",
            "a slow morning is a luxury",
        ),
        captions=(
            "Nothing fancy. Just clean sheets, cold water and being on time.",
            "The whole personality is: hydrated and five minutes early.",
            "Simplicity is the flex nobody talks about.",
            "Saving this for the mornings I don't feel like it.",
        ),
        ctas=("Which one are you starting with?", "Save this for tomorrow morning.",
              "Tell me your non-negotiable.", "Comment your slow-morning ritual."),
        hashtags_broad=("#cleangirl", "#aesthetic", "#morningroutine"),
        hashtags_niche=("#cleangirlaesthetic", "#slowliving", "#thatgirl", "#softlife"),
        hashtags_micro=("#morningmotivation", "#minimalaesthetic", "#neutralaesthetic",
                        "#dailyritual", "#romanticisingmylife"),
        signoff="save for later",
    ),
    music="Soft house or a slowed R&B loop, 90-105 BPM, no lyrics in the first 2 seconds.",
    keywords=("linen", "coffee", "marble", "skincare", "white", "morning", "minimal"),
))

register(Preset(
    key="dark-academia",
    title="Dark Academia",
    blurb="Libraries, ink, wool and rain. Candlelight on old paper.",
    palette_hex=("#1b1611", "#3a2f24", "#5d4a35", "#8a7350", "#b9a37c", "#d8cbb2"),
    brightness=0.28, saturation=0.34, contrast=0.62, warmth=0.30, hue_spread=0.20,
    weights=_weights(palette=3.6, brightness=2.0, contrast=1.5, warmth=1.6),
    grade=Grade(
        contrast=1.14, brightness=-0.04, saturation=0.82, gamma=0.94,
        shadows=(-0.02, -0.01, 0.04), midtones=(0.03, 0.01, -0.03), highlights=(0.05, 0.02, -0.05),
        grain=7.0, vignette=0.38,
    ),
    motion=Motion(styles=("push_in", "drift_up", "pull_out"), zoom=0.09, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Georgia.ttf",
               "/System/Library/Fonts/Supplemental/Times New Roman.ttf"),
        case="title", size_ratio=0.062, color="#e8dcc2", letter_spacing=3, position="center",
    ),
    pacing=Pacing(beats_per_shot=(2, 4), min_shot=1.1, max_shot=2.8,
                  transition=("fade", "fadeblack"), transition_seconds=0.42),
    copy=Copy(
        hooks=(
            "Studying Like The Semester Owes Me Money",
            "Books, Rain, And No Notifications",
            "Romanticise The Reading List",
            "One More Chapter Energy",
            "The Library At Closing Time",
        ),
        captions=(
            "Nobody is coming to make you interesting. Read the book.",
            "Autumn is a personality and I'm committing to the bit.",
            "Discipline dressed as atmosphere.",
            "Rain on the window is the only study playlist I need.",
        ),
        ctas=("What are you reading right now?", "Drop your current chapter.",
              "Save for your next study session."),
        hashtags_broad=("#darkacademia", "#studytok", "#booktok"),
        hashtags_niche=("#darkacademiaaesthetic", "#studymotivation", "#academiaaesthetic",
                        "#studyinspo"),
        hashtags_micro=("#autumnacademia", "#libraryaesthetic", "#readingnook",
                        "#annotatedbooks", "#studywithme"),
        signoff="one more chapter",
    ),
    music="Piano or lo-fi strings, 70-90 BPM. Rain layer underneath sells it.",
    keywords=("book", "library", "candle", "rain", "wool", "ink", "archive", "museum"),
))

register(Preset(
    key="coastal-linen",
    title="Coastal Linen",
    blurb="Salt air, bleached wood and white cotton drying in the wind.",
    palette_hex=("#f6f4ef", "#dfe6e6", "#b7c9c9", "#8aa7ac", "#5c7f88", "#e3d9c6"),
    brightness=0.70, saturation=0.28, contrast=0.36, warmth=-0.10, hue_spread=0.26,
    weights=_weights(palette=3.2, brightness=1.9, warmth=1.5, saturation=1.2),
    grade=Grade(
        contrast=1.05, brightness=0.04, saturation=0.94, gamma=1.04,
        shadows=(-0.02, 0.0, 0.04), midtones=(0.0, 0.01, 0.02), highlights=(0.03, 0.02, 0.0),
        grain=2.5, vignette=0.12,
    ),
    motion=Motion(styles=("pan_left", "pan_right", "pull_out", "drift_up"), zoom=0.06, drift=0.07),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/AvenirNext.ttc",),
        case="lower", size_ratio=0.058, color="#ffffff", letter_spacing=5, position="lower",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=1.0, max_shot=2.6,
                  transition=("fade", "dissolve", "smoothleft"), transition_seconds=0.4),
    copy=Copy(
        hooks=(
            "salt water fixes most of it",
            "the summer i stopped rushing",
            "sea air as a personality trait",
            "pov: no signal, no plans",
            "linen weather",
        ),
        captions=(
            "Everything looks solvable near the water.",
            "Sun, salt, and absolutely nowhere to be.",
            "Packing light and leaving early.",
            "This is the feeling I'm chasing all winter.",
        ),
        ctas=("Where's your water place?", "Tag who you'd bring.",
              "Save this for your next trip."),
        hashtags_broad=("#coastal", "#summeraesthetic", "#travel"),
        hashtags_niche=("#coastalgrandmother", "#coastalaesthetic", "#slowsummer",
                        "#seasidevibes"),
        hashtags_micro=("#linenseason", "#saltyair", "#beachmornings", "#quietluxury",
                        "#coastalliving"),
        signoff="see you by the water",
    ),
    music="Airy indie or a soft bossa loop, 95-110 BPM.",
    keywords=("ocean", "beach", "sand", "boat", "linen", "shell", "coast", "sail"),
))

register(Preset(
    key="old-money",
    title="Old Money",
    blurb="Tailoring, horses, marble halls and no visible logos.",
    palette_hex=("#efe9dd", "#d6c9ad", "#9c8c6b", "#4d4a3d", "#2b3a34", "#1d1c19"),
    brightness=0.46, saturation=0.30, contrast=0.52, warmth=0.16, hue_spread=0.24,
    weights=_weights(palette=3.5, contrast=1.4, saturation=1.4, quality=2.0),
    grade=Grade(
        contrast=1.10, brightness=0.0, saturation=0.86, gamma=0.99,
        shadows=(0.0, 0.01, 0.02), midtones=(0.02, 0.01, -0.02), highlights=(0.04, 0.03, -0.02),
        grain=4.0, vignette=0.26,
    ),
    motion=Motion(styles=("push_in", "pan_right", "pull_out"), zoom=0.06, drift=0.04),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Georgia.ttf",),
        case="title", size_ratio=0.058, color="#f2ead9", letter_spacing=4, position="upper",
    ),
    pacing=Pacing(beats_per_shot=(2, 4), min_shot=1.1, max_shot=3.0,
                  transition=("fade", "dissolve"), transition_seconds=0.45),
    copy=Copy(
        hooks=(
            "Quiet Luxury Is A Discipline",
            "No Logos, Just Standards",
            "Old Money Habits That Cost Nothing",
            "Taste Is Free. Restraint Is The Hard Part.",
            "Dress For The Life You're Building",
        ),
        captions=(
            "The label goes on the inside for a reason.",
            "Buy less. Keep it longer. Take care of it.",
            "Understated on purpose.",
            "Class isn't the price tag, it's the upkeep.",
        ),
        ctas=("Which habit are you stealing?", "Save this one.",
              "Agree or overrated? Tell me."),
        hashtags_broad=("#oldmoney", "#quietluxury", "#aesthetic"),
        hashtags_niche=("#oldmoneyaesthetic", "#stealthwealth", "#classicstyle",
                        "#timelessstyle"),
        hashtags_micro=("#oldmoneyvibes", "#understatedluxury", "#capsulewardrobe",
                        "#tailoring", "#heritagestyle"),
        signoff="quietly, always",
    ),
    music="Strings or a muted jazz standard, 80-100 BPM.",
    keywords=("tailored", "estate", "horse", "marble", "leather", "tennis", "villa"),
))

register(Preset(
    key="tokyo-night",
    title="Tokyo Night",
    blurb="Wet asphalt, vending machine glow, neon bleeding into the rain.",
    palette_hex=("#0a0d18", "#122036", "#1f4a6b", "#3f8fb0", "#c9436a", "#e8c46a"),
    brightness=0.24, saturation=0.62, contrast=0.74, warmth=-0.18, hue_spread=0.62,
    weights=_weights(palette=3.0, brightness=2.2, contrast=1.8, saturation=1.8, hue_spread=0.4),
    grade=Grade(
        contrast=1.20, brightness=-0.03, saturation=1.12, gamma=0.92,
        shadows=(-0.04, -0.01, 0.08), midtones=(0.0, 0.0, 0.03), highlights=(0.04, -0.01, 0.02),
        grain=8.0, vignette=0.34, blur_bloom=0.25,
    ),
    motion=Motion(styles=("push_in", "pan_left", "drift_up"), zoom=0.10, drift=0.07),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="upper", size_ratio=0.064, color="#f2f6ff", letter_spacing=8, position="lower",
    ),
    pacing=Pacing(beats_per_shot=(1, 2), min_shot=0.55, max_shot=1.8,
                  transition=("fade", "smoothleft", "pixelize"), transition_seconds=0.2),
    copy=Copy(
        hooks=(
            "3AM IN A CITY THAT NEVER SITS DOWN",
            "NEON AND NOBODY WAITING UP",
            "THE WALK HOME HITS DIFFERENT",
            "CITY POP AND WET STREETS",
            "PUT THIS ON AT NIGHT",
        ),
        captions=(
            "Best conversations happen after midnight.",
            "Rain, neon, convenience store coffee. That's the whole night.",
            "Walking nowhere in particular.",
            "Turn the sound on for this one.",
        ),
        ctas=("Headphones on or off?", "Where's your 3am city?",
              "Save for your night walk."),
        hashtags_broad=("#tokyo", "#nightvibes", "#cityaesthetic"),
        hashtags_niche=("#tokyonight", "#citypop", "#nightwalk", "#neonaesthetic"),
        hashtags_micro=("#shibuya", "#rainyday", "#japanaesthetic", "#lofivibes",
                        "#streetsatnight"),
        signoff="see you at 3am",
    ),
    music="City pop, future funk or drum-heavy lo-fi, 100-120 BPM.",
    keywords=("neon", "night", "city", "rain", "street", "tokyo", "sign", "subway"),
))

register(Preset(
    key="cottagecore",
    title="Cottagecore",
    blurb="Bread, wildflowers, hand-me-down quilts and a garden that needs weeding.",
    palette_hex=("#f3ead6", "#dfd09f", "#b6bb7c", "#7d8a53", "#c98f78", "#5c4a37"),
    brightness=0.60, saturation=0.44, contrast=0.42, warmth=0.24, hue_spread=0.48,
    weights=_weights(palette=3.2, warmth=1.6, saturation=1.3, hue_spread=0.6),
    grade=Grade(
        contrast=1.03, brightness=0.03, saturation=1.02, gamma=1.05,
        shadows=(0.02, 0.02, 0.0), midtones=(0.03, 0.02, -0.02), highlights=(0.04, 0.03, -0.02),
        grain=5.0, vignette=0.20,
    ),
    motion=Motion(styles=("drift_up", "pan_right", "pull_out"), zoom=0.07, drift=0.07),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Georgia.ttf",),
        case="lower", size_ratio=0.060, color="#fff8ea", letter_spacing=2, position="upper",
    ),
    pacing=Pacing(beats_per_shot=(2, 4), min_shot=1.0, max_shot=2.8,
                  transition=("fade", "dissolve"), transition_seconds=0.38),
    copy=Copy(
        hooks=(
            "a slow saturday in the garden",
            "everything homemade, nothing rushed",
            "the bread rose and so did i",
            "touching grass, literally",
            "small life, big joy",
        ),
        captions=(
            "Nothing here is efficient and that's the point.",
            "Made with my hands, eaten too fast.",
            "The garden doesn't care about my inbox.",
            "Slow is not the same as behind.",
        ),
        ctas=("What would you bake first?", "Save for your slow weekend.",
              "Garden or kitchen, pick one."),
        hashtags_broad=("#cottagecore", "#slowliving", "#homemade"),
        hashtags_niche=("#cottagecoreaesthetic", "#gardentok", "#sourdough", "#farmhouse"),
        hashtags_micro=("#wildflowers", "#homesteading", "#bakingfromscratch",
                        "#simplepleasures", "#countryliving"),
        signoff="slow living, always",
    ),
    music="Folk guitar or accordion, 90-110 BPM, warm and acoustic.",
    keywords=("garden", "flower", "bread", "kitchen", "field", "cottage", "farm", "picnic"),
))

register(Preset(
    key="y2k-chrome",
    title="Y2K Chrome",
    blurb="Butterfly clips, lip gloss, chrome and a flip phone flash.",
    palette_hex=("#e9f2ff", "#a9d8f0", "#7f8fd6", "#d78fc4", "#f2c1d8", "#2a2b45"),
    brightness=0.58, saturation=0.58, contrast=0.60, warmth=-0.12, hue_spread=0.58,
    weights=_weights(palette=2.8, saturation=2.0, hue_spread=0.5, contrast=1.3),
    grade=Grade(
        contrast=1.16, brightness=0.02, saturation=1.20, gamma=1.0,
        shadows=(-0.03, 0.0, 0.06), midtones=(0.02, 0.0, 0.04), highlights=(0.03, 0.01, 0.05),
        grain=6.0, vignette=0.10, blur_bloom=0.30,
    ),
    motion=Motion(styles=("push_in", "pan_left", "pan_right"), zoom=0.11, drift=0.08),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="upper", size_ratio=0.064, color="#ffffff", letter_spacing=6, position="center",
        box=True, box_color="#1b1b3a66",
    ),
    pacing=Pacing(beats_per_shot=(1,), min_shot=0.45, max_shot=1.4,
                  transition=("fade", "pixelize", "circleopen"), transition_seconds=0.16),
    copy=Copy(
        hooks=(
            "IF YOU KNOW YOU KNOW",
            "BRINGING THIS BACK, SORRY NOT SORRY",
            "2003 CALLED AND IT WAS RIGHT",
            "THE FLASH PHOTO ERA WAS PEAK",
            "NOSTALGIA IN 10 SECONDS",
        ),
        captions=(
            "We peaked and nobody told us.",
            "Bring back the flip phone flash.",
            "Everything old is expensive again.",
            "Tell me you were there without telling me.",
        ),
        ctas=("Were you there?", "Rate this era out of 10.",
              "Which one are you bringing back?"),
        hashtags_broad=("#y2k", "#nostalgia", "#2000s"),
        hashtags_niche=("#y2kaesthetic", "#y2kfashion", "#2000score", "#nostalgiacore"),
        hashtags_micro=("#throwbackstyle", "#chromeaesthetic", "#buterflyclips",
                        "#digicam", "#earlyinternet"),
        signoff="if you know, you know",
    ),
    music="Early-2000s pop, eurodance or hyperpop edit, 120-135 BPM.",
    keywords=("chrome", "glitter", "gloss", "pink", "flash", "y2k", "sparkle"),
))

register(Preset(
    key="parisian",
    title="Parisian",
    blurb="Balconies, espresso, cigarettes you don't smoke and grey stone light.",
    palette_hex=("#e8e4dc", "#c9c2b4", "#9a9286", "#6a6459", "#3a3730", "#b0574a"),
    brightness=0.52, saturation=0.26, contrast=0.48, warmth=0.06, hue_spread=0.22,
    weights=_weights(palette=3.4, saturation=1.7, brightness=1.4, hue_spread=1.2),
    grade=Grade(
        contrast=1.08, brightness=0.01, saturation=0.80, gamma=1.01,
        shadows=(0.0, 0.0, 0.03), midtones=(0.01, 0.0, 0.0), highlights=(0.03, 0.02, -0.01),
        grain=5.5, vignette=0.24,
    ),
    motion=Motion(styles=("pan_right", "push_in", "drift_up"), zoom=0.06, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Times New Roman.ttf",
               "/System/Library/Fonts/Supplemental/Georgia.ttf"),
        case="lower", size_ratio=0.058, color="#fbf8f2", letter_spacing=5, position="lower",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=1.0, max_shot=2.5,
                  transition=("fade", "dissolve"), transition_seconds=0.36),
    copy=Copy(
        hooks=(
            "an ordinary tuesday in paris",
            "the coffee is small and the day is long",
            "pov: you live here now",
            "nobody is in a hurry here",
            "je ne sais quoi, explained",
        ),
        captions=(
            "One espresso, two hours, zero regrets.",
            "The city rewards people who walk slowly.",
            "Grey skies, good bread, still perfect.",
            "Saving this for the version of me who books the flight.",
        ),
        ctas=("Would you live here?", "Save this for your trip list.",
              "Coffee or wine, choose."),
        hashtags_broad=("#paris", "#travel", "#aesthetic"),
        hashtags_niche=("#parisaesthetic", "#frenchgirl", "#parisianstyle", "#europeansummer"),
        hashtags_micro=("#cafeculture", "#parismoments", "#frenchaesthetic",
                        "#slowtravel", "#balconyviews"),
        signoff="à bientôt",
    ),
    music="French pop, accordion or soft jazz, 85-105 BPM.",
    keywords=("paris", "cafe", "balcony", "croissant", "street", "wine", "stone"),
))

register(Preset(
    key="desert-minimal",
    title="Desert Minimal",
    blurb="Adobe walls, long shadows, terracotta and heat you can see.",
    palette_hex=("#f1e2cf", "#e0b18a", "#c07a4f", "#8d4f34", "#4c3a30", "#dcd2c0"),
    brightness=0.58, saturation=0.48, contrast=0.56, warmth=0.42, hue_spread=0.22,
    weights=_weights(palette=3.6, warmth=2.2, contrast=1.4, hue_spread=1.2),
    grade=Grade(
        contrast=1.10, brightness=0.02, saturation=1.0, gamma=1.0,
        shadows=(0.02, 0.0, 0.0), midtones=(0.04, 0.01, -0.03), highlights=(0.05, 0.02, -0.04),
        grain=4.5, vignette=0.22,
    ),
    motion=Motion(styles=("push_in", "pan_left", "pull_out"), zoom=0.07, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="upper", size_ratio=0.058, color="#fdf3e5", letter_spacing=7, position="lower",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=1.0, max_shot=2.7,
                  transition=("fade", "dissolve", "smoothright"), transition_seconds=0.34),
    copy=Copy(
        hooks=(
            "HEAT, SHADOW, NOTHING ELSE",
            "THE DESERT DOESN'T NEGOTIATE",
            "GO WHERE THE SIGNAL DIES",
            "SUNSET TAKES AN HOUR HERE",
            "EMPTY ON PURPOSE",
        ),
        captions=(
            "Silence is the amenity.",
            "Everything out here is the colour of clay.",
            "Long drive, no plan, better for it.",
            "Turning the phone off after this.",
        ),
        ctas=("Desert or ocean?", "Save for your next drive.",
              "Where would you take this road?"),
        hashtags_broad=("#desert", "#travel", "#minimal"),
        hashtags_niche=("#desertaesthetic", "#adobe", "#southwest", "#roadtrip"),
        hashtags_micro=("#terracotta", "#goldenhour", "#desertvibes", "#slowtravel",
                        "#offgrid"),
        signoff="see you out there",
    ),
    music="Desert blues, slide guitar or downtempo, 85-100 BPM.",
    keywords=("desert", "sand", "adobe", "cactus", "canyon", "clay", "road", "dune"),
))

register(Preset(
    key="soft-grunge",
    title="Soft Grunge",
    blurb="Grey weather, denim, film scratches and a good sulk.",
    palette_hex=("#d9d9dd", "#a2a4ac", "#6c6f7a", "#43454f", "#252630", "#7c5f6a"),
    brightness=0.38, saturation=0.20, contrast=0.58, warmth=-0.08, hue_spread=0.24,
    weights=_weights(palette=3.0, saturation=2.0, brightness=1.6, contrast=1.5),
    grade=Grade(
        contrast=1.13, brightness=-0.02, saturation=0.72, gamma=0.96,
        shadows=(-0.02, 0.0, 0.04), midtones=(0.0, 0.0, 0.02), highlights=(0.02, 0.0, 0.03),
        grain=10.0, vignette=0.32,
    ),
    motion=Motion(styles=("drift_up", "pan_left", "push_in"), zoom=0.08, drift=0.06),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="lower", size_ratio=0.058, color="#eceff4", letter_spacing=3, position="center",
    ),
    pacing=Pacing(beats_per_shot=(1, 2), min_shot=0.6, max_shot=1.9,
                  transition=("fade", "fadeblack", "smoothup"), transition_seconds=0.24),
    copy=Copy(
        hooks=(
            "grey days are underrated",
            "nothing happened and it was fine",
            "the in-between weeks",
            "some months are just weather",
            "put this on and stare out a window",
        ),
        captions=(
            "Not sad. Just quiet.",
            "The weather is doing my emotional labour.",
            "Nothing to fix today.",
            "This is the one you send with no caption.",
        ),
        ctas=("Anyone else?", "Save if this is your month.",
              "Send this to the one person who gets it."),
        hashtags_broad=("#grunge", "#aesthetic", "#moody"),
        hashtags_niche=("#softgrunge", "#filmphotography", "#moodyaesthetic", "#grungeaesthetic"),
        hashtags_micro=("#greydays", "#35mm", "#filmgrain", "#overcast", "#quietmood"),
        signoff="stay soft",
    ),
    music="Shoegaze, slowed alt-rock or a reverb-heavy loop, 75-95 BPM.",
    keywords=("grey", "denim", "rain", "film", "concrete", "smoke", "window"),
))

register(Preset(
    key="pilates-princess",
    title="Pilates Princess",
    blurb="Matcha, reformer studios, ribbed sets and 7am discipline.",
    palette_hex=("#f6f1ea", "#e3ded4", "#c3cbb4", "#8fa07f", "#cbb2a0", "#4a4740"),
    brightness=0.66, saturation=0.28, contrast=0.36, warmth=0.04, hue_spread=0.34,
    weights=_weights(palette=3.2, brightness=1.9, saturation=1.5),
    grade=Grade(
        contrast=1.05, brightness=0.04, saturation=0.92, gamma=1.04,
        shadows=(0.0, 0.01, 0.01), midtones=(0.01, 0.02, 0.0), highlights=(0.03, 0.03, 0.0),
        grain=2.5, vignette=0.14,
    ),
    motion=Motion(styles=("push_in", "pan_right", "drift_up"), zoom=0.07, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/AvenirNext.ttc",),
        case="lower", size_ratio=0.060, color="#fffdf8", letter_spacing=4, position="upper",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=0.8, max_shot=2.1,
                  transition=("fade", "dissolve"), transition_seconds=0.28),
    copy=Copy(
        hooks=(
            "the 7am class changed my life",
            "matcha, movement, minding my business",
            "strong is the whole aesthetic",
            "things i do instead of doomscrolling",
            "one hour to myself, every day",
        ),
        captions=(
            "Showing up is the entire strategy.",
            "Not for the aesthetic. Okay, partly for the aesthetic.",
            "Sore in the good way.",
            "Save this for the morning you want to skip.",
        ),
        ctas=("What's your 7am ritual?", "Save for Monday.",
              "Matcha or coffee, be honest."),
        hashtags_broad=("#pilates", "#wellness", "#fitness"),
        hashtags_niche=("#pilatesprincess", "#reformerpilates", "#wellnessroutine",
                        "#matchagirl"),
        hashtags_micro=("#morningmovement", "#pilatesbody", "#healthygirlera",
                        "#studioflow", "#greenjuice"),
        signoff="see you at 7",
    ),
    music="Soft house or a chilled edit of a pop track, 100-115 BPM.",
    keywords=("pilates", "matcha", "studio", "gym", "yoga", "green", "workout"),
))

register(Preset(
    key="vanilla-girl",
    title="Vanilla Girl",
    blurb="Cream knits, candles, cashmere and everything the colour of milk.",
    palette_hex=("#fbf7f0", "#f0e6d8", "#e0d0bb", "#c5ae94", "#9b8770", "#5a4f43"),
    brightness=0.74, saturation=0.20, contrast=0.28, warmth=0.20, hue_spread=0.14,
    weights=_weights(palette=3.8, brightness=2.2, saturation=2.0, hue_spread=1.6),
    grade=Grade(
        contrast=1.02, brightness=0.05, saturation=0.84, gamma=1.06,
        shadows=(0.02, 0.01, 0.0), midtones=(0.03, 0.01, -0.02), highlights=(0.04, 0.02, -0.02),
        grain=3.0, vignette=0.14, blur_bloom=0.20,
    ),
    motion=Motion(styles=("push_in", "drift_up"), zoom=0.06, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="lower", size_ratio=0.058, color="#fffefb", letter_spacing=5, position="center",
    ),
    pacing=Pacing(beats_per_shot=(2,), min_shot=1.0, max_shot=2.5,
                  transition=("fade", "dissolve"), transition_seconds=0.4),
    copy=Copy(
        hooks=(
            "everything soft, nothing loud",
            "cream is a personality",
            "the cosiest ten seconds of your day",
            "warm lighting fixes everything",
            "your sign to light the candle",
        ),
        captions=(
            "Soft is not the same as small.",
            "Candle lit, phone down, nowhere to be.",
            "Comfort as a design principle.",
            "Save this for a bad day.",
        ),
        ctas=("Candle or fairy lights?", "Save for the next cosy night.",
              "What's your comfort ritual?"),
        hashtags_broad=("#cozy", "#aesthetic", "#vanillagirl"),
        hashtags_niche=("#vanillagirlaesthetic", "#cozyaesthetic", "#neutralhome",
                        "#softaesthetic"),
        hashtags_micro=("#creamaesthetic", "#candlelight", "#knitwear", "#hygge",
                        "#warmtones"),
        signoff="stay warm",
    ),
    music="Slowed bedroom pop or ambient piano, 80-95 BPM.",
    keywords=("cream", "candle", "knit", "cashmere", "vanilla", "cosy", "beige"),
))

register(Preset(
    key="street-mono",
    title="Street Mono",
    blurb="Black and white city frames. Contrast, geometry, strangers in motion.",
    palette_hex=("#ffffff", "#c9c9c9", "#8f8f8f", "#565656", "#2a2a2a", "#000000"),
    brightness=0.44, saturation=0.05, contrast=0.82, warmth=0.0, hue_spread=0.05,
    weights=_weights(palette=2.0, contrast=2.6, saturation=2.4, quality=2.0, hue_spread=0.2),
    grade=Grade(
        contrast=1.22, brightness=0.0, saturation=0.0, gamma=0.97,
        grain=9.0, vignette=0.30,
    ),
    motion=Motion(styles=("push_in", "pan_left", "pan_right"), zoom=0.08, drift=0.05),
    typography=Typography(
        fonts=("/System/Library/Fonts/Supplemental/Futura.ttc",),
        case="upper", size_ratio=0.054, color="#ffffff", letter_spacing=9, position="lower",
    ),
    pacing=Pacing(beats_per_shot=(1, 2), min_shot=0.6, max_shot=2.0,
                  transition=("fade", "fadeblack"), transition_seconds=0.22),
    copy=Copy(
        hooks=(
            "SHOT ON A WALK, NOTHING PLANNED",
            "THE CITY IS THE SUBJECT",
            "BLACK AND WHITE HIDES NOTHING",
            "STRANGERS, GEOMETRY, LIGHT",
            "ONE ROLL, ONE AFTERNOON",
        ),
        captions=(
            "No colour, no distractions.",
            "Everyone here is going somewhere important.",
            "Light does most of the work.",
            "Frame 12 was the one.",
        ),
        ctas=("Which frame?", "Colour or mono, pick a side.",
              "Save if you shoot street."),
        hashtags_broad=("#streetphotography", "#blackandwhite", "#photography"),
        hashtags_niche=("#streetphoto", "#monochrome", "#filmphotography", "#bnwphotography"),
        hashtags_micro=("#candidstreet", "#urbanphotography", "#35mmfilm",
                        "#lightandshadow", "#shotonfilm"),
        signoff="keep walking",
    ),
    music="Upright bass, boom-bap or minimal techno, 90-110 BPM.",
    keywords=("street", "city", "mono", "shadow", "crowd", "architecture", "urban"),
))
