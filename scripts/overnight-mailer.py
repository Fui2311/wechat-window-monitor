"""Send three authorized local test reports; never wakes or controls the service."""
import argparse
from datetime import datetime, timedelta, timezone
from html import escape
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
from zoneinfo import ZoneInfo

from wechat_tracker.config import load_config
from wechat_tracker.credentials import apply_credentials
from wechat_tracker.notify import send_email
from wechat_tracker.service import read_status

spec = importlib.util.spec_from_file_location("overnight_observation", Path(__file__).with_name("overnight-monitor.py"))
observation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observation)
BEIJING = ZoneInfo("Asia/Shanghai")
PHASES = ("夜间监测报告", "08点后自主运行检查", "上午自主运行结果")


def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def sleep_events(since, until=None):
    start = datetime.fromisoformat(since)
    end = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)
    output = subprocess.run(["/usr/bin/pmset", "-g", "log"], capture_output=True, text=True,
                            timeout=20, check=True).stdout
    events = []
    for line in output.splitlines():
        match = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(Sleep|Wake|DarkWake)\s+(?=Entering Sleep|(?:Dark)?Wake from)", line)
        if not match:
            continue
        at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S %z")
        if not start <= at <= end:
            continue
        reason = re.search(r"Entering Sleep state due to '([^']+)'", line)
        events.append(dict(at=at.isoformat(), event=match.group(2),
                           reason=reason.group(1) if reason else None))
    return events


def snapshot(directory, manifest):
    config = load_config(manifest["mail_config"])
    if config["group"] != manifest["group"] or Path(config["data_dir"]).resolve() != directory / "data":
        raise ValueError("The report must remain bound to the approved main-group data")
    report = observation.summarize(observation.read_records(directory), manifest)
    state = read_status(config)
    report["current_service"] = {key: state.get(key) for key in observation.SERVICE_FIELDS}
    report["power_now"] = observation.power_state()
    report["sleep_events"] = sleep_events(manifest["created_at"])
    observer = directory / "observer.json"
    report["observer"] = json.loads(observer.read_text()) if observer.is_file() else {}
    report["report_created_at"] = observation.stamp()
    return config, report


def render(manifest, report, index, due):
    state = report["current_service"]
    now = datetime.fromisoformat(report["report_created_at"])
    delay = max(0, round((now - datetime.fromisoformat(due)).total_seconds()))
    captured = state.get("last_capture_at")
    captured_at = datetime.fromisoformat(captured.replace("Z", "+00:00")) if captured else None
    autonomous_fresh = (captured_at is not None and captured_at >= datetime.fromisoformat(manifest["deadline"])
                        and -2 <= (now-captured_at).total_seconds() <= 45)
    ready = report["fresh_capture_samples"] > 0 if index == 0 else autonomous_fresh
    status = ("已观察到成功采集" if ready else "尚未观察到成功采集，不能判定监控可用")
    subject = f"[微信群监控测试] {PHASES[index]}：{status}"
    error = state.get("last_error")
    friendly_error = {"target_window_not_ready": "主群独立窗口当前不可读取",
                      "accessibility_permission_required": "辅助功能权限不可用"}.get(error, error or "无")
    power = report["power_now"]
    lid = power.get("lid", {}).get("AppleClamshellState")
    blockers = power.get("sleep_prevention_owners", [])
    blocker_names = sorted({item["process"] for item in blockers})
    assistant_blockers = [name for name in blocker_names if name.casefold() in {"codex", "chatgpt"}]
    lines = [
        PHASES[index], "",
        f"监控群：{manifest['group']}",
        f"预定报告时间：{local_time(due)}（北京时间）",
        f"实际生成时间：{local_time(report['report_created_at'])}（北京时间）",
        f"延迟：{delay} 秒；电脑休眠时任务不会强行唤醒系统。", "",
        "主群服务当前状态",
        f"服务进程存活：{'是' if state.get('running') else '否'}",
        f"当前状态：{state.get('status') or '未知'}；最近错误：{friendly_error}",
        f"最近成功采集：{local_time(state['last_capture_at']) if state.get('last_capture_at') else '尚无'}",
        f"服务累计快照：{state.get('snapshots') or 0}；采集子进程重启：{state.get('worker_restarts') or 0}",
        f"讲座通知：已发送 {state.get('email_sent') or 0} 封，发送失败 {state.get('email_failures') or 0} 次。", "",
        "夜间记录结果",
        f"采样 {report['samples']} 次，采集时间仍新鲜的样本 {report['fresh_capture_samples']} 次。",
        f"独立只读核验 {report['deep_probes']} 次，成功 {report['deep_probe_successes']} 次。",
        f"采样中断区间：{len(report['sampling_gaps'])} 段。",
        f"结论：{status}。", "",
        "当前条件与08点后的独立阶段",
        f"合盖：{'是' if lid is True else '否' if lid is False else '未知'}；电源：{power.get('power_source') or '未知'}。",
        f"系统 SleepDisabled 标记：{power.get('sleep_disabled') if power.get('sleep_disabled') is not None else '未知'}（状态标记不替代实际睡眠记录）。",
        f"测试条件：{manifest.get('test_condition', '按既定夜测条件记录')}。",
        f"当前持有防休眠状态的进程：{'、'.join(blocker_names) or '未检出'}。",
        (f"独立性限制：仍检测到 {'、'.join(assistant_blockers)} 持有防空闲休眠状态，不能判定已排除它们的保活影响。"
         if assistant_blockers else "本次报告采样未检出 Codex 或 ChatGPT 持有防休眠状态；这不证明整个独立阶段均已排除。"),
        "按你的选择保留 Amphetamine 当前设置；主群服务保留自身的防休眠设置。",
        f"夜间记录器状态：{report.get('observer',{}).get('state','未知')}。",
        "08:00 后停止 Codex 定时回访和夜间记录器，由本机 LaunchAgent 独立维持主群服务。",
        "晨间邮件也是本机独立任务发送；测试结束不会关闭主群服务。", "",
        "测试期间系统睡眠与唤醒记录",
    ]
    for event in report["sleep_events"][-12:]:
        labels = {"Sleep": "睡眠", "Wake": "唤醒", "DarkWake": "后台短暂唤醒"}
        lines.append(f"{local_time(event['at'])}：{labels[event['event']]}" +
                     (f"（{event['reason']}）" if event.get("reason") else ""))
    if not report["sleep_events"]:
        lines.append("本次查询窗口没有发现睡眠/唤醒事件。")
    lines += ["", "这封邮件是服务测试报告，不是新讲座公告。",
              "找不到主群窗口时，不能仅凭进程运行或没有新邮件就判断消息监控正常。",
              "邮件发送记录以 SMTP 接受为准，收件箱到达情况请以邮箱中实际收到为准。"]
    body = "\n".join(lines)
    paragraphs = "".join(f'<p style="margin:0 0 10px;white-space:pre-wrap">{escape(block)}</p>'
                         for block in body.split("\n\n"))
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><body '
            'style="margin:0;padding:20px;background:#f5f6f8;font-family:-apple-system,Arial,sans-serif;line-height:1.65">'
            '<div style="max-width:640px;margin:auto;padding:24px;background:white;border-radius:12px">' +
            paragraphs + '</div></body></html>')
    return subject, body, html


def send_once(directory, manifest, index, due):
    path = directory / "mail-journal.json"
    journal = json.loads(path.read_text()) if path.is_file() else {}
    # A crash after submission can leave delivery uncertain; never silently
    # resend that milestone and create duplicate test reports.
    if due in journal:
        if journal[due].get("status") == "sending":
            journal[due].update(status="uncertain", checked_at=observation.stamp())
            observation.save(path, journal)
        return journal[due]
    entry = dict(phase=PHASES[index], scheduled_at=due, attempted_at=observation.stamp(), status="sending")
    journal[due] = entry
    observation.save(path, journal)
    try:
        config, report = snapshot(directory, manifest)
        subject, body, html = render(manifest, report, index, due)
        observation.save(directory / f"mail-report-{index}.json", report)
        if not apply_credentials(config):
            raise RuntimeError("Authorized existing email configuration is unavailable")
        send_email(subject, body, config["email"], html_body=html)
    except Exception as error:
        entry.update(status="failed", error_type=type(error).__name__, finished_at=observation.stamp())
    else:
        entry.update(status="smtp_accepted", finished_at=observation.stamp(),
                     late_seconds=max(0, round((datetime.now(timezone.utc) - datetime.fromisoformat(due)).total_seconds())))
    journal[due] = entry
    observation.save(path, journal)
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "preview"))
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("morning_mail_authorized") is not True:
        raise ValueError("Morning report delivery must be explicitly authorized")
    times = manifest["morning_mail_times"]
    if len(times) != 3:
        raise ValueError("This test has exactly three bounded report milestones")
    if args.action == "preview":
        _, report = snapshot(directory, manifest)
        subject, body, html = render(manifest, report, 0, times[0])
        (directory / "mail-preview.txt").write_text(subject + "\n\n" + body)
        (directory / "mail-preview.html").write_text(html)
        print(json.dumps({"preview_written": True, "smtp_sent": False}, ensure_ascii=False))
        return
    if datetime.now(timezone.utc) > datetime.fromisoformat(manifest["autonomous_until"]) + timedelta(days=1):
        observation.save(directory / "mailer.json", dict(state="expired_no_mail", ended_at=observation.stamp()))
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    observation.save(directory / "mailer.json", dict(pid=os.getpid(), state="waiting", started_at=observation.stamp(),
        runtime_package=str(Path(__import__('wechat_tracker').__file__).resolve()),
        service_controls=False, wake_requests=False))
    for index, due in enumerate(times):
        while not stop.is_set():
            remaining = (datetime.fromisoformat(due) - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            stop.wait(min(60, remaining))
        if stop.is_set():
            break
        send_once(directory, manifest, index, due)
    observation.save(directory / "mailer.json", dict(pid=os.getpid(), state="stopped" if stop.is_set() else "completed",
        ended_at=observation.stamp(), service_controls=False, wake_requests=False))


if __name__ == "__main__":
    main()
