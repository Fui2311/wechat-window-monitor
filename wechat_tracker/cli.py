import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from .calendar import export_ics
from .capture import CaptureError
from .config import load_config
from .engine import Engine, parse_time
from .store import Store


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"JSONL第{line_number}行格式错误") from error


def main(argv=None):
    # Windows redirected terminals may default to GBK, which cannot render emoji
    # from the source evidence. Files and CLI output use the same UTF-8 contract.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="指定微信群讲座捕获、分析与邮箱提醒（默认仅预览）")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--db", help="覆盖SQLite路径，用于隔离测试数据")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="只读检查目标群窗口与采集能力")
    ingest = sub.add_parser("ingest", help="导入标准化JSONL，可含转发子记录")
    ingest.add_argument("path")
    ingest.add_argument("--now", help="带时区的评估时间（只用于可重复回放）")
    forward = sub.add_parser("forward", help="检查已打开的合并转发详情，显式绑定来源父卡片")
    forward.add_argument("--parent-id", required=True, help="来源群卡片ID或人工明确绑定的本地ID")
    forward.add_argument("--window-title", default="群聊的聊天记录", help="详情窗口的精确标题")
    forward.add_argument("--snapshot", help="从已保存的详情快照读取，避免再次操作微信")
    forward.add_argument("--save", help="同时保存标准化的父子消息JSONL")
    sub.add_parser("events", help="列出已识别事件与待核实线索")
    sub.add_parser("outbox", help="查看待发任务、失败和版本")
    tick = sub.add_parser("tick", help="预览或发送当前到期的邮件")
    tick.add_argument("--send", action="store_true", help="真实连接已配置SMTP并发送")
    tick.add_argument("--now", help="仅预览允许模拟时间")
    watch = sub.add_parser("watch", help="轮询当前指定群窗口；默认仅预览")
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--interval", type=int, default=15)
    watch.add_argument("--include-existing", action="store_true", help="首次捕获也分析现有可见消息")
    watch.add_argument("--send", action="store_true", help="真实发送邮件")
    calendar = sub.add_parser("calendar", help="导出确定日程的ICS（时间等有歧义的线索不导出）")
    calendar.add_argument("path")
    demo = sub.add_parser("demo", help="截图样例回放；使用独立demo.sqlite3，不真实发送")
    demo.add_argument("--now", default="2026-10-15T13:00:00+08:00")
    args = parser.parse_args(argv)
    store = None
    try:
        config = load_config(args.config)
        if args.command == "probe":
            from .capture import probe
            result = probe(config)
            output(result)
            return 0 if result.get("ok") else 2
        db = Path(args.db) if args.db else Path(config["data_dir"]) / (
            "demo.sqlite3" if args.command == "demo" else "tracker.sqlite3")
        store = Store(db)
        engine = Engine(store, config)
        if args.command in ("ingest", "demo"):
            source = args.path if args.command == "ingest" else Path(__file__).resolve().parent / "resources" / "screenshot.jsonl"
            now = parse_time(args.now) if args.now else None
            for message in read_jsonl(source):
                output(engine.ingest(message, now))
            if args.command == "demo":
                previews = engine.dispatch(now, send=False)
                preview_path = Path(config["data_dir"]) / "demo-email-preview.txt"
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                preview_path.write_text("\n\n".join(p["subject"] + "\n" + p["body"] for p in previews), encoding="utf-8")
                calendar_path = Path(config["data_dir"]) / "demo.ics"
                calendar_path.write_bytes(export_ics(store.events()).encode("utf-8"))
                output({"database": str(db.resolve()), "email_preview": str(preview_path),
                        "calendar": str(calendar_path), "due_previews": len(previews),
                        "notice": "人工转写的截图样例；未发送邮件；名单PDF正文未读取"})
        elif args.command == "events":
            output(store.events())
        elif args.command == "forward":
            from .capture import read_forward
            config["capture"] = dict(config["capture"], forward_window_title=args.window_title,
                                     forward_parent_id=args.parent_id)
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig")) if args.snapshot else None
            parent = read_forward(config, args.parent_id, snapshot=snapshot)
            if args.save:
                target = Path(args.save)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(parent, ensure_ascii=False) + "\n", encoding="utf-8")
            result = engine.ingest(parent)
            output({"children": len(parent.get("children", [])), "source_meta": parent.get("source_meta"),
                    "result": result, "notice": "仅分析；未发送邮件。通用详情标题无法独立证明来源群，已按显式父ID绑定。"})
        elif args.command == "outbox":
            jobs = store.jobs()
            for job in jobs:
                job["payload"] = json.loads(job["payload"])
            output(jobs)
        elif args.command == "tick":
            if args.send and args.now:
                raise ValueError("真实发送不能使用模拟时间")
            output(engine.dispatch(parse_time(args.now) if args.now else None, args.send))
        elif args.command == "calendar":
            Path(args.path).write_bytes(export_ics(store.events()).encode("utf-8"))
            output({"calendar": str(Path(args.path).resolve())})
        elif args.command == "watch":
            if args.interval < 5:
                raise ValueError("轮询间隔至少5秒")
            from .capture import capture_once
            while True:
                try:
                    messages = capture_once(config)
                    ingested = engine.capture_snapshot(messages, args.include_existing)
                    output({"capture_ok": True, "observed_messages": len(messages), "ingested": ingested})
                except Exception as error:
                    output({"capture_ok": False, "error_type": type(error).__name__, "error": str(error),
                            "notice": "本轮未捕获消息；请检查登录、指定独立群窗口及可见状态"})
                    # Existing reminders still run when Weixin is unavailable.
                    output(engine.dispatch(send=args.send))
                    if args.once:
                        return 2
                else:
                    output(engine.dispatch(send=args.send))
                if args.once:
                    break
                time.sleep(args.interval)
        return 0
    except (ValueError, OSError, KeyError, CaptureError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已停止；消息和待发任务已保存在SQLite。", file=sys.stderr)
        return 130
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
