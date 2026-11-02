"""Readable notification alternatives. Source time is never inferred from capture time."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .recognition.schema import evidence_text


def display_time(value):
    if not value:
        return "未读取"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return "时间无时区，待核对"
        return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (ValueError, TypeError, AttributeError):
        return "时间格式无效，待核对"


def format_match_mail(window, payload, result, *, received_at=None, record_id=None):
    source = window["name"]
    sent = display_time(payload.get("sent_at"))
    observed = display_time(payload.get("observed_at") or received_at)
    sender = payload.get("sender") or "未读取"
    original = evidence_text(payload) or "（无可读正文）"
    if payload.get("kind") == "forward" and payload.get("children"):
        parts = []
        for index, child in enumerate(payload["children"], 1):
            content = evidence_text(child)
            if child.get("kind") == "forward" and not child.get("children"):
                content = "嵌套转发正文尚未读取"
            parts.append(
                f"【转发内第{index}条】\n发送者：{child.get('sender') or '未读取'}\n发送时间：{display_time(child.get('sent_at'))}\n{content}"
            )
        original = "\n\n".join(parts)
        if payload.get("incomplete"):
            original += "\n\n读取范围：仅包含已加载可访问的消息，完整历史未证明。"
    reason = result.get("reason") or "符合当前识别目标"
    evidence = result.get("evidence") or ""
    subject = "[微信提醒] " + " ".join(source.split())[:80]
    times = f"消息发送时间：{sent}\n采集时间：{observed}\n时区：北京时间（UTC+8）"
    extra_times = []

    def child_times(node, depth=0):
        if depth > 6:
            return
        for child in node.get("children", []):
            if isinstance(child, dict):
                if child.get("sent_at"):
                    extra_times.append(display_time(child["sent_at"]))
                child_times(child, depth + 1)

    child_times(payload)
    child_label = "；".join(extra_times)
    note = (
        "发送时间未从微信读取，采集时间仅表示本机发现消息的时间。"
        if not payload.get("sent_at")
        else ""
    )
    plain = f"微信消息提醒\n来自：{source}\n发送者：{sender}\n{times}\n"
    if child_label:
        plain += f"转发内消息发送时间：{child_label}\n"
    if note:
        plain += note + "\n"
    plain += f"\n判断依据\n{reason}\n\n消息原文\n{original}\n"
    if record_id is not None:
        plain += f"\n记录编号：{record_id}\n"
    highlighted = escape(original)
    if evidence and evidence in original:
        safe = escape(evidence)
        highlighted = highlighted.replace(
            safe,
            f'<mark style="background:#dcfce7;color:#14532d;padding:2px 0">{safe}</mark>',
        )
    fields = [
        ("来自会话", source),
        ("发送者", sender),
        ("消息发送时间", sent),
        ("采集时间", observed),
    ]
    if child_label:
        fields.append(("转发内消息时间", child_label))
    metadata = "".join(
        f'<tr><td style="padding:5px 0;width:112px;vertical-align:top;color:#64748b;font-size:13px">{escape(label)}</td><td style="padding:5px 0;color:#0f172a;font-size:14px;word-break:break-word">{escape(str(value))}</td></tr>'
        for label, value in fields
    )
    note_html = (
        f'<p style="margin:10px 0 0;color:#64748b;font-size:12px;line-height:1.6">{escape(note)}</p>'
        if note
        else ""
    )
    footer = f"记录编号 {escape(str(record_id))} · " if record_id is not None else ""
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>微信消息提醒</title>
<style>@media(max-width:480px){{.outer{{padding:8px!important}}.content{{padding:18px 14px!important}}}}</style></head>
<body style="margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;color:#0f172a">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td class="outer" align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:white;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;table-layout:fixed">
<tr><td class="content" style="padding:24px;border-top:5px solid #2563eb">
<p style="margin:0 0 10px;color:#15803d;font-size:13px;font-weight:600">● LLM 识别命中</p>
<h1 style="margin:0 0 6px;font-size:23px;line-height:1.4">微信消息提醒</h1>
<p style="margin:0 0 20px;font-size:14px;color:#64748b;overflow-wrap:anywhere">{escape(source)}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">{metadata}</table>
<p style="margin:6px 0 0;font-size:12px;color:#64748b">以上时间均为北京时间（UTC+8）</p>{note_html}
<div style="margin:22px 0;padding:14px 16px;background:#eff6ff;border-left:3px solid #2563eb;border-radius:6px">
<h2 style="margin:0 0 6px;font-size:14px;color:#1d4ed8">判断依据</h2><p style="margin:0;font-size:15px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere">{escape(reason)}</p></div>
<h2 style="margin:0 0 10px;font-size:16px">消息原文</h2>
<div style="font-size:16px;line-height:1.85;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere">{highlighted}</div>
<p style="margin:24px 0 0;padding-top:14px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8">{footer}本机监控提醒 · 仅依据已读取内容</p>
</td></tr></table></td></tr></table></body></html>"""
    return subject, plain, html
