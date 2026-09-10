"""Project configuration, stored as JSON in ``.mbtok/config.json``.

Everything here has a working default, so ``mbtok make`` does something useful
before the file exists. Running ``mbtok init`` writes it out with the folders
it found, which is mostly a convenience so the user is not retyping paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ledger import DEFAULT_COOLDOWN_DAYS
from .util import expand, read_json, state_dir, write_json


@dataclass
class Config:
    """Per-project settings."""

    #: Folders scanned for owned media.
    roots: list[str] = field(default_factory=list)
    #: Where rendered videos and their sidecars are written.
    output_dir: str = "out"
    #: Folder of music tracks used when no track is named explicitly.
    music_dir: str = ""
    #: Default mood when the user does not pass one.
    preset: str = "clean-girl"
    #: Default video length in seconds.
    duration: float = 10.0
    #: Days an asset rests before it may appear in another post.
    cooldown_days: float = DEFAULT_COOLDOWN_DAYS
    #: Lowest acceptable per-asset quality score.
    min_quality: float = 0.35
    #: How hard the curator pushes for variety, 0-1.
    diversity: float = 0.55
    #: Optional handle rendered as the sign-off instead of the preset's.
    handle: str = ""
    #: Output frame size and rate.
    width: int = 1080
    height: int = 1920
    fps: int = 30
    #: Encoder settings.
    crf: int = 20
    max_bitrate_mbps: float = 14.0
    #: Explicit font file for text overlays; empty means auto-detect.
    font: str = ""

    @classmethod
    def load(cls, root: Path | str = ".") -> "Config":
        """Read the project config, falling back to defaults."""
        data = read_json(state_dir(root) / "config.json", default={})
        if not isinstance(data, dict):
            return cls()
        known = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def save(self, root: Path | str = ".") -> Path:
        """Write the config and return its path."""
        path = state_dir(root, create=True) / "config.json"
        write_json(path, self.__dict__)
        return path

    def resolved_roots(self) -> list[Path]:
        """Configured scan roots as absolute paths."""
        return [expand(root) for root in self.roots]

    def resolved_output(self, root: Path | str = ".") -> Path:
        """Output folder as an absolute path."""
        candidate = Path(self.output_dir).expanduser()
        if candidate.is_absolute():
            return candidate
        return Path(root).expanduser().resolve() / candidate
