#!/usr/bin/env python3
"""Start or reuse the local macOS console, then open it in the default browser."""
import argparse
import fcntl
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wechat_tracker.config import load_config, resolve_config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="覆盖自动发现的本机配置")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--no-open", action="store_true", help="仅启动或检查，不打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")

    config_path = resolve_config_path(args.config).resolve()
    config = load_config(config_path)
    url = f"http://127.0.0.1:{args.port}/"
    # Bypass shell proxy settings for this strictly local health check.
    opener = build_opener(ProxyHandler({}))

    def ready():
        try:
            with opener.open(url + "api/status", timeout=2) as response:
                data = json.load(response)
        except URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                return False
            raise RuntimeError(f"控制台端口无响应或已被其他服务占用：{url}") from error
        if not isinstance(data, dict):
            raise RuntimeError(f"端口已被其他服务占用：{url}")
        safe_config = data.get("config", {})
        if (data.get("ok") is not True or not isinstance(data.get("service"), dict)
                or not isinstance(safe_config, dict) or not safe_config.get("data_dir")):
            raise RuntimeError(f"端口已被其他服务占用：{url}")
        if Path(safe_config["data_dir"]).resolve() != Path(config["data_dir"]).resolve():
            raise RuntimeError(f"该端口上的控制台使用另一份配置，请指定其他 --port：{url}")
        return True

    state = ROOT / "state"
    state.mkdir(parents=True, exist_ok=True)
    # Serialize simultaneous clicks, including those from another launcher process.
    with (state / f"web-console-{args.port}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready():
            print(f"控制台已运行，复用现有服务：{url}")
        else:
            log_path = state / "web-console.log"
            with log_path.open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-u", "-m", "wechat_tracker", "--config",
                     str(config_path), "web", "--port", str(args.port)],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True,
                )
            try:
                deadline = time.monotonic() + 15
                while not ready():
                    if child.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"控制台启动失败，请查看日志：{log_path}")
                    time.sleep(0.2)
            except Exception:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
                raise
            pid_path = state / ("web-console.pid" if args.port == 8088
                                else f"web-console-{args.port}.pid")
            pid_path.write_text(str(child.pid) + "\n", encoding="utf-8")
            print(f"控制台已在后台启动：{url}")
        if not args.no_open:
            subprocess.run(["/usr/bin/open", url], check=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, socket.timeout) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        raise SystemExit(1)
