#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$DIR/.." && pwd)"
if [ -d "$REPO_DIR/wechat_tracker" ]; then
  export PYTHONPATH="$REPO_DIR:${PYTHONPATH:-}"
fi
BASE="$HOME/Library/Application Support/WeChatLectureTracker"
PYTHON="$BASE/.venv/bin/python"
CONFIG="$BASE/config.toml"
if [ "$#" -eq 0 ]; then set -- status; fi
if [ ! -x "$PYTHON" ]; then
  echo "Run install.command first."
  exit 1
fi
case "$1" in
  authorize) exec /usr/bin/open "$HOME/Applications/WeChat Lecture Tracker Capture.app" ;;
  verify) shift; exec "$PYTHON" "$BASE/mac/verify.py" --config "$CONFIG" "$@" ;;
  email) shift; if [ "$#" -eq 0 ]; then set -- status; fi; exec "$PYTHON" -m wechat_tracker --config "$CONFIG" email "$@" ;;
  probe|events|outbox|calendar|web) exec "$PYTHON" -m wechat_tracker --config "$CONFIG" "$@" ;;
  run|start|stop|restart|status|install|uninstall) exec "$PYTHON" -m wechat_tracker --config "$CONFIG" service "$@" ;;
  *) echo "Usage: manage.command {authorize|verify|probe|web|install --send|start|stop|restart|status|uninstall|email setup|email status|events|outbox|calendar PATH}"; exit 2 ;;
esac
