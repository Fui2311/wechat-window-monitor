"""Shared, root-relative discovery filters; do not traverse snapshots or hidden trees."""
import os
from pathlib import Path


def ignored_discovery_path(relative: Path) -> bool:
    return any(part.startswith(".") or any(word in part.lower()
               for word in ("backup", "before", "previous", "last-good")) for part in relative.parts)


def database_candidates(root: Path):
    """Explicit roots remain valid; exclusions apply to their discovered children."""
    for directory, children, files in os.walk(root, topdown=True, followlinks=False):
        folder = Path(directory)
        children[:] = sorted(name for name in children if not ignored_discovery_path(Path(name)))
        for name in sorted(files):
            candidate = folder / name
            if (name.lower().endswith(".sqlite3") and not name.lower().endswith(".tmp.sqlite3")
                    and not ignored_discovery_path(Path(name)) and not candidate.is_symlink()):
                yield candidate
