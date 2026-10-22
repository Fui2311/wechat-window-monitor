#!/usr/bin/env python3
"""Cross-platform launcher for WeChat Lecture Tracker Native Web Dashboard."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wechat_tracker.cli import main

if __name__ == "__main__":
    args = ["web", "--open"] + sys.argv[1:]
    raise SystemExit(main(args))
