#!/usr/bin/env python3
"""Open the installed console, starting its on-demand LaunchAgent if needed."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


BASE = Path.home() / "Library/Application Support/WeChatLectureTracker"
URL = "http://127.0.0.1:8088/"
LABEL = "local.wechat-lecture-tracker.console"
LOGS = Path.home() / "Library/Logs/WeChatLectureTracker"


def main() -> int:
    python = BASE / ".venv/bin/python"
    config = BASE / "config.toml"
    if not python.is_file() or not config.is_file():
        raise RuntimeError("未找到本机已安装的讲座监控程序或配置")
    LOGS.mkdir(parents=True, exist_ok=True)
    http = build_opener(ProxyHandler({}))

    def ready() -> bool:
        try:
            with http.open(URL + "api/status", timeout=2) as response:
                data = json.load(response)
        except (URLError, OSError):
            return False
        if not isinstance(data, dict) or data.get("ok") is not True or not isinstance(data.get("service"), dict):
            raise RuntimeError("8088 端口上的服务不是讲座控制台")
        return True

    with (LOGS / "console-launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not ready():
            # launchd starts the installed Python independently of Shortcuts;
            # the launcher itself never needs to read the Documents folder.
            domain = f"gui/{os.getuid()}"
            service = domain + "/" + LABEL
            arguments = [str(python), "-u", "-m", "wechat_tracker", "--config", str(config), "web", "--port", "8088"]
            plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
            loaded = subprocess.run(["/bin/launchctl", "print", service], capture_output=True)
            if loaded.returncode not in (0, 3, 113):
                raise RuntimeError("无法检查控制台启动项状态")
            if plist.exists():
                existing = plistlib.loads(plist.read_bytes())
                if existing.get("Label") != LABEL or existing.get("ProgramArguments") != arguments:
                    raise RuntimeError("同名控制台启动项属于另一份安装，未覆盖")
            elif loaded.returncode == 0:
                raise RuntimeError("同名控制台服务已加载但缺少可核验的启动项，未修改")
            else:
                plist.parent.mkdir(parents=True, exist_ok=True)
                payload = dict(Label=LABEL, ProgramArguments=arguments, WorkingDirectory=str(BASE),
                               RunAtLoad=False, KeepAlive=False, ProcessType="Background",
                               StandardOutPath=str(LOGS / "web-console.log"),
                               StandardErrorPath=str(LOGS / "web-console.log"))
                descriptor, temporary = tempfile.mkstemp(prefix=LABEL + ".", suffix=".tmp", dir=plist.parent)
                try:
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "wb") as handle:
                        plistlib.dump(payload, handle)
                    os.replace(temporary, plist)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            if loaded.returncode != 0:
                subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True)
            subprocess.run(["/bin/launchctl", "kickstart", service], check=True, capture_output=True)
            deadline = time.monotonic() + 20
            while not ready():
                if time.monotonic() >= deadline:
                    raise RuntimeError("控制台未能启动，请查看 Library/Logs/WeChatLectureTracker/web-console.log")
                time.sleep(0.2)
        subprocess.run(["/usr/bin/open", URL], check=True)
    print("讲座控制台已打开：" + URL)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"启动失败：{error}")
        raise SystemExit(1)
