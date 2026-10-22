#!/usr/bin/env python3
"""Build a macOS agent application (.app) to launch the lecture tracker web console."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


DEFAULT_APP_NAME = "讲座消息控制台.app"
BUNDLE_ID = "local.wechat-lecture-tracker.console-launcher"
EXECUTABLE_NAME = "console-launcher"


def resolve_repo_dir(custom_repo: str | Path | None = None) -> Path:
    """Resolve the project root directory."""
    if custom_repo:
        repo = Path(custom_repo).resolve()
    else:
        repo = Path(__file__).resolve().parent.parent
    web_command = repo / "mac" / "web.command"
    if not web_command.is_file():
        raise FileNotFoundError(f"未在项目目录中找到 mac/web.command: {web_command}")
    return repo


def check_existing_target(target_app: Path) -> None:
    """Refuse to overwrite if target exists and is not this launcher's bundle."""
    if not target_app.exists():
        return
    plist_path = target_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise RuntimeError(f"目标路径已存在但缺少 Contents/Info.plist: {target_app}，拒绝覆盖")
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
    except Exception as error:
        raise RuntimeError(f"读取已有 App 的 Info.plist 失败: {error}，拒绝覆盖") from error

    existing_id = data.get("CFBundleIdentifier")
    if existing_id != BUNDLE_ID:
        raise RuntimeError(
            f"目标路径已存在同名 App，但 Bundle ID 不匹配 (已有: {existing_id!r}, 预期: {BUNDLE_ID!r})，拒绝覆盖"
        )


def build_launcher(
    repo_dir: Path | None = None,
    target_app: Path | None = None,
) -> Path:
    """Build the macOS .app launcher bundle."""
    repo = resolve_repo_dir(repo_dir)
    helper_source = repo / "mac" / "open-console-installed.py"
    if not helper_source.is_file():
        raise FileNotFoundError(f"缺少已安装控制台启动器: {helper_source}")

    if target_app is None:
        target_app = Path.home() / "Applications" / DEFAULT_APP_NAME
    else:
        target_app = Path(target_app).resolve()

    check_existing_target(target_app)

    contents_dir = target_app / "Contents"
    macos_dir = contents_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    # 1. Info.plist
    plist_content = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": "讲座消息控制台",
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIconFile": "",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "讲座消息控制台",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.4",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    plist_path = contents_dir / "Info.plist"
    with plist_path.open("wb") as f:
        plistlib.dump(plist_content, f, fmt=plistlib.FMT_XML)

    # 2. PkgInfo
    pkginfo_path = contents_dir / "PkgInfo"
    pkginfo_path.write_bytes(b"APPL????")

    # 3. CFBundleExecutable shell script
    resources_dir = contents_dir / "Resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(helper_source, resources_dir / "open-console-installed.py")
    launcher_script = """#!/bin/bash
set -euo pipefail
CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TRACKER_BASE="$HOME/Library/Application Support/WeChatLectureTracker"
LOG_DIR="$HOME/Library/Logs/WeChatLectureTracker"
mkdir -p "$LOG_DIR"
exec "$TRACKER_BASE/.venv/bin/python" "$CONTENTS_DIR/Resources/open-console-installed.py" >> "$LOG_DIR/console-launcher.log" 2>&1
"""
    launcher_path = macos_dir / EXECUTABLE_NAME
    launcher_path.write_text(launcher_script, encoding="utf-8")

    # Set executable mode: 0o755
    launcher_path.chmod(0o755)

    return target_app


def validate_app(target_app: Path) -> dict[str, str | bool]:
    """Validate plist syntax, bundle identifiers, and shell script syntax."""
    target_app = Path(target_app).resolve()
    if not target_app.is_dir():
        raise FileNotFoundError(f"App 目录不存在: {target_app}")

    plist_path = target_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise FileNotFoundError(f"缺少 Info.plist: {plist_path}")

    with plist_path.open("rb") as f:
        plist_data = plistlib.load(f)

    if plist_data.get("CFBundleIdentifier") != BUNDLE_ID:
        raise ValueError(f"CFBundleIdentifier 错误: {plist_data.get('CFBundleIdentifier')}")

    if plist_data.get("LSUIElement") is not True:
        raise ValueError(f"LSUIElement 未设置为 True: {plist_data.get('LSUIElement')}")

    if plist_data.get("CFBundleExecutable") != EXECUTABLE_NAME:
        raise ValueError(f"CFBundleExecutable 错误: {plist_data.get('CFBundleExecutable')}")

    # plutil check
    plutil_res = subprocess.run(
        ["/usr/bin/plutil", "-lint", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if plutil_res.returncode != 0:
        raise RuntimeError(f"plutil 校验失败: {plutil_res.stderr.strip() or plutil_res.stdout.strip()}")

    launcher_path = target_app / "Contents" / "MacOS" / EXECUTABLE_NAME
    if not launcher_path.is_file():
        raise FileNotFoundError(f"缺少可执行文件: {launcher_path}")

    if not os.access(launcher_path, os.X_OK):
        raise PermissionError(f"启动脚本缺少可执行权限: {launcher_path}")

    # bash -n syntax check
    bash_res = subprocess.run(
        ["/bin/bash", "-n", str(launcher_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if bash_res.returncode != 0:
        raise RuntimeError(f"bash -n 校验失败: {bash_res.stderr.strip()}")

    return {
        "app_path": str(target_app),
        "bundle_id": str(plist_data.get("CFBundleIdentifier")),
        "ls_ui_element": bool(plist_data.get("LSUIElement")),
        "executable": str(launcher_path),
        "plutil_lint": "OK",
        "bash_syntax_check": "OK",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / "Applications" / DEFAULT_APP_NAME,
        help="目标 .app 路径 (默认: ~/Applications/讲座消息控制台.app)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="项目根目录 (默认根据脚本自身位置自动识别)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="仅校验已有的 .app，不重新构建",
    )
    args = parser.parse_args()

    try:
        if args.validate_only:
            result = validate_app(args.target)
            print(f"校验成功: {result}")
            return 0

        target = build_launcher(repo_dir=args.repo, target_app=args.target)
        result = validate_app(target)
        print(f"构建并校验成功: {result}")
        return 0
    except Exception as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
