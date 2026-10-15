#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != Darwin ]; then
  echo "Run this installer on macOS."
  exit 1
fi
for candidate in "${WECHAT_PYTHON:-}" python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
    exec "$candidate" mac/install.py
  fi
done
echo "Python 3.11+ is required. Install from https://www.python.org/downloads/macos/ or use brew install python."
exit 1
