"""Create an explicit local configuration from packaged, credential-free templates."""
import json
from pathlib import Path
import sys

from .paths import RESOURCE_ROOT


def initialize_config(path: str | Path, *, platform: str | None = None, group: str | None = None) -> Path:
    platform = platform or ("macos" if sys.platform == "darwin" else "windows")
    if platform not in {"macos", "windows", "jsonl"}:
        raise ValueError("platform 必须为 macos、windows 或 jsonl")
    if group is not None and (not group.strip() or len(group) > 200 or any(ord(c) < 32 for c in group)):
        raise ValueError("请输入完整、有效的微信群名")
    template = (RESOURCE_ROOT / f"config.{'macos' if platform == 'macos' else 'windows'}.toml").read_text(encoding="utf-8")
    if group is not None:
        template = template.replace('group = "2026级计算机研究生通知群"', 'group = ' + json.dumps(group.strip(), ensure_ascii=False), 1)
    if platform == "macos":
        helper = Path.home() / "Applications/WeChat Lecture Tracker Capture.app/Contents/MacOS/wechat-capture"
        template = template.replace('"__MAC_HELPER__"', json.dumps(str(helper), ensure_ascii=False))
    elif platform == "jsonl":
        template = template.replace('source = "windows_uia"', 'source = "jsonl"\njsonl_path = ' + json.dumps(str(RESOURCE_ROOT / "screenshot.jsonl"), ensure_ascii=False))
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation never overwrites an existing user's configuration.
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(template)
    target.chmod(0o600)
    return target
