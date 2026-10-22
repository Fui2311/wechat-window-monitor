#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DIR/.." && pwd)"
if [ -d "$REPO_DIR/wechat_tracker" ]; then
  export PYTHONPATH="$REPO_DIR:${PYTHONPATH:-}"
fi

BASE="$HOME/Library/Application Support/WeChatLectureTracker"
PYTHON=""
if [ -x "$BASE/.venv/bin/python" ]; then
  PYTHON="$BASE/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "错误: 未找到可执行的 Python 3 环境。"
  exit 1
fi

exec "$PYTHON" "$REPO_DIR/scripts/open-web-console.py" "$@"
