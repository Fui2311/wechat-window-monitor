"""Standalone foreground dashboard; never discovers or controls legacy services."""

import argparse
import json
from pathlib import Path
import signal
import threading
import webbrowser
from . import __version__
from .application import Application
from .web import MonitorServer
from .locking import WorkspaceLock


def main(argv=None):
    parser = argparse.ArgumentParser(description="精确名称微信窗口监控 Dashboard")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / ".wechat-window-monitor"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动本地多窗口控制台，默认全部监控停止")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    sub.add_parser("demo", help="生成独立的离线演示窗口与合成规则，不访问微信或模型")
    args = parser.parse_args(argv)
    try:
        with WorkspaceLock(args.data_dir):
            return run(args)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"无法启动：{exc}\n")


def run(args):
    app = Application(args.data_dir)
    if args.command == "demo":
        fixture = app.repo.root / "demo.jsonl"
        fixture.write_text(
            json.dumps(
                {
                    "id": "demo-1",
                    "group": "合成演示窗口",
                    "sender": "示例发送者",
                    "text": "项目构建失败，请检查日志",
                    "kind": "text",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        existing = next(
            (w for w in app.repo.list_windows() if w["name"] == "合成演示窗口"), None
        )
        if existing is None:
            window = app.save_window(
                dict(
                    name="合成演示窗口",
                    target="提醒项目构建失败",
                    mode="local",
                    include_existing=True,
                    capture=dict(source="jsonl", jsonl_path=str(fixture)),
                )
            )
            rule = app.save_rule(
                window["id"],
                dict(
                    schema_version=1,
                    mode="local",
                    target=window["target"],
                    instructions="离线合成示例：包含构建失败",
                    match={
                        "any": [
                            {"field": "text", "op": "contains", "value": "构建失败"}
                        ],
                        "all": [],
                        "none": [],
                    },
                ),
            )
            app.activate(window["id"], rule["id"])
        print(
            json.dumps(
                {
                    "ok": True,
                    "notice": "离线演示已创建；启动serve后选择合成演示窗口并开始监控",
                },
                ensure_ascii=False,
            )
        )
        return 0
    try:
        server = MonitorServer(("127.0.0.1", args.port), app)
    except Exception:
        app.close()
        raise

    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(
        f"WeChat Window Monitor {__version__}: http://127.0.0.1:{server.server_port}",
        flush=True,
    )
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        app.close()
    return 0
