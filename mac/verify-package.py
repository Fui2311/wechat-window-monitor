"""Verify every shipped file before installation; no third-party dependencies."""
import hashlib
from pathlib import Path
import sys


def verify(root):
    root = Path(root).resolve()
    listed = set()
    for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        original = root / name
        target = original.resolve()
        if not target.is_relative_to(root) or name in listed or original.is_symlink():
            raise ValueError("Unsafe manifest path")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Checksum mismatch: " + name)
        listed.add(name)
    actual = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and p.name not in {"SHA256SUMS.txt", ".DS_Store"}}
    if actual != listed:
        raise ValueError("The manifest does not cover the full package")
    if any(Path(name).suffix.lower() in {".ps1", ".cmd", ".bat", ".exe", ".sqlite3", ".pyc"}
           or "local.toml" in name or ".credentials" in name for name in listed):
        raise ValueError("Platform-specific or local data found in package")
    return len(listed)


if __name__ == "__main__":
    try:
        print("Package SHA256 checks passed:", verify(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent), "files")
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
