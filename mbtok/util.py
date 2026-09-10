"""Small shared helpers: paths, hashing, deterministic randomness, text."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import unicodedata
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, TypeVar

T = TypeVar("T")

#: Directory (relative to the project root) that holds the library index,
#: usage ledger and other derived state.
STATE_DIRNAME = ".mbtok"


def state_dir(root: Path | str = ".", create: bool = False) -> Path:
    """Return the ``.mbtok`` state directory for *root*."""
    path = Path(root).expanduser().resolve() / STATE_DIRNAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def expand(path: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and environment variables, then resolve to an absolute path."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def fingerprint(path: Path) -> str:
    """Cheap content fingerprint: size + mtime + head/tail bytes.

    Full hashing of multi-gigabyte stock footage is wasteful, and the parts of
    a media file that change when it is re-exported are almost always in the
    first or last few kilobytes (container headers and indexes).
    """
    stat = path.stat()
    digest = hashlib.sha1()
    digest.update(str(stat.st_size).encode())
    digest.update(str(int(stat.st_mtime)).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(65536))
        if stat.st_size > 131072:
            handle.seek(-65536, os.SEEK_END)
            digest.update(handle.read(65536))
    return digest.hexdigest()


def stable_seed(*parts: Any) -> int:
    """A deterministic integer seed derived from *parts*.

    ``random.seed("text")`` is stable across runs but not across Python
    versions for every type, so hash the string form explicitly.
    """
    joined = "\x1f".join(str(part) for part in parts)
    return int(hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16], 16)


def rng(*parts: Any) -> random.Random:
    """A seeded :class:`random.Random` so a given request always renders the same."""
    return random.Random(stable_seed(*parts))


def clamp(value: float, low: float, high: float) -> float:
    """Constrain *value* to the inclusive range [*low*, *high*]."""
    return max(low, min(high, value))


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between *a* and *b*."""
    return a + (b - a) * t


def slugify(text: str, max_length: int = 60) -> str:
    """Turn arbitrary text into a filesystem- and URL-safe slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (slug[:max_length].rstrip("-")) or "untitled"


def human_duration(seconds: float) -> str:
    """Format *seconds* as ``M:SS.s`` for logs and reports."""
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}"


def chunked(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    """Yield consecutive slices of *items* of at most *size* elements."""
    for start in range(0, len(items), max(1, size)):
        yield items[start : start + size]


def dedupe(items: Iterable[T]) -> list[T]:
    """Order-preserving de-duplication."""
    seen: set[T] = set()
    result: list[T] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, paths and sets into JSON-safe values."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(to_jsonable(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, payload: Any) -> None:
    """Write *payload* as pretty JSON, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from *path*, returning *default* when missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
