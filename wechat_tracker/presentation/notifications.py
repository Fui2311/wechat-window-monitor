"""Render captured evidence as text/HTML without transport or persistence."""
from __future__ import annotations
import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import urlsplit

class EmailConfigurationError(ValueError):
    """A notification or SMTP configuration cannot be used safely."""


class EmailNotificationError(RuntimeError):
    """A single SMTP attempt failed; the caller may apply its retry policy."""


_BEIJING = timezone(timedelta(hours=8))
_REASONS = {
    "new": "新增",
    "update": "变更",
    "cancelled": "取消",
    "reminder": "到时提醒",
    "review": "待核实",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _display_datetime(value: Any) -> str:
    if not value:
        return "未识别，请核对原文"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return f"{value.isoformat()}（具体时间待核实）"
    else:
        raw = _text(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return f"{raw}（具体时间待核实）"
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return f"{raw}（时间格式待核实）"
    if parsed.tzinfo is None:
        return f"{parsed:%Y-%m-%d %H:%M}（时区待核实）"
    return f"{parsed.astimezone(_BEIJING):%Y-%m-%d %H:%M}（北京时间）"


def _validate_notification(event: dict, reason: str) -> None:
    if not isinstance(event, dict):
        raise EmailConfigurationError("event 必须是对象。")
    if reason not in _REASONS:
        raise EmailConfigurationError("未知通知原因；允许 new/update/cancelled/reminder/review。")


def _notice_fields(event: dict) -> dict[str, str]:
    """Use captured source verbatim; parsed fields never decide mail content."""
    raw = event.get("source_text")
    raw = raw if isinstance(raw, str) else ""
    evidence = event.get("evidence")
    note = _text(evidence.get("content_note")) if isinstance(evidence, dict) else ""
    if event.get("processing_method") == "rules_fallback":
        note = "；".join(filter(None, (note, "LLM 提取失败，已回退本地规则，以下为采集原文")))
    if event.get("source_kind") == "file":
        filename = _text(event.get("title")) or "未注明文件名"
        return dict(title="文件：" + filename, body="文件：" + filename,
                    note=note or "文件消息；仅记录文件名，未下载、未读取文件正文")
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    return dict(title=first_line or _text(event.get("title")) or "讲座消息",
                body=raw if raw.strip() else "原文未保存。", note=note)


def notification_format(event: dict) -> str:
    """Select from the message's own processing result, never today's settings."""
    if event.get("category") != "lecture_notice":
        return "legacy"
    if event.get("source_kind") == "file":
        return "source"
    return "llm_summary" if event.get("processing_method") == "llm" else "source"


def _extracted_notice_fields(event: dict) -> list[tuple[str, str]]:
    def time_value(field, raw_field):
        if event.get(field):
            return _display_datetime(event[field])
        return _text(event.get(raw_field)) or "未注明"

    activity = time_value("start_at", "activity_time_text")
    if event.get("start_at") and event.get("end_at"):
        activity += " 至 " + _display_datetime(event["end_at"])
    url = _text(event.get("signup_url"))
    # Only captured literal web URLs may become a signup link in mail.
    literal_urls = re.findall(r"https?://[^\s<>\"'，。；！？、（）【】《》]+", _text(event.get("source_text")))
    if url not in literal_urls:
        url = ""
    return [("讲座主题", _text(event.get("title")) or "未注明"),
            ("讲座活动时间", activity),
            ("报名开始时间", time_value("signup_start_at", "signup_start_text")),
            ("报名链接", url or "未注明"),
            ("内容摘要", _text(event.get("summary")) or "未注明")]


def _source_html(raw: str) -> str:
    """Escape all source markup, linking only literal HTTP(S) URLs in place."""
    pieces, offset = [], 0
    for match in re.finditer(r"https?://[^\s<>\"'，。；！？、（）【】《》]+", raw):
        pieces.append(escape(raw[offset:match.start()], quote=True))
        value = match.group(0)
        try:
            parsed = urlsplit(value)
            linked = bool(parsed.netloc) and parsed.scheme.lower() in {"http", "https"}
        except ValueError:
            linked = False
        value = escape(value, quote=True)
        pieces.append(f'<a href="{value}" target="_blank" rel="noopener noreferrer">{value}</a>' if linked else value)
        offset = match.end()
    pieces.append(escape(raw[offset:], quote=True))
    return "".join(pieces)


def format_notification(event: dict, reason: str) -> tuple[str, str]:
    """Send lecture notices as source text, retaining legacy event formatting."""
    _validate_notification(event, reason)
    title = _text(event.get("title")) or "未识别标题"
    # Subjects must remain one line even when source-derived titles contain newlines.
    subject_title = " ".join(title.split())[:180]
    if event.get("category") == "lecture_notice":
        fields = _notice_fields(event)
        if notification_format(event) == "llm_summary":
            extracted = _extracted_notice_fields(event)
            subject_title = " ".join(extracted[0][1].split())[:80]
            message = "LLM 提取信息（请核对下方原文）\n" + "\n".join(
                f"{label}：{value}" for label, value in extracted)
            message += "\n\n讲座消息原文：\n" + fields["body"]
            if fields["note"]:
                message += "\n\n提示：" + fields["note"]
            return f"[讲座·LLM] {subject_title}", message
        subject_title = " ".join(fields["title"].split())
        if len(subject_title) > 80:
            subject_title = subject_title[:79].rstrip() + "…"
        message = fields["body"]
        if fields["note"]:
            message += "\n\n提示：" + fields["note"]
        return f"[讲座] {subject_title}", message
    subject = f"[微信群通知·{_REASONS[reason]}] {subject_title}"
    summary = " ".join((_text(event.get("summary")) or f"讲座《{title}》，内容未注明。").split())[:500]
    cancelled = reason == "cancelled" or event.get("status") == "cancelled"
    if cancelled:
        summary = "【已取消】" + summary
    elif reason == "update":
        summary = "【通知更新】" + summary
    elif reason == "review":
        summary = "【待核实】" + summary
    deadline = _display_datetime(event["deadline_at"]) if event.get("deadline_at") else (
        " ".join(_text(event.get("deadline_text")).split())[:400] + "（待核实）" if event.get("deadline_text") else "未注明")
    if event.get("start_at") and event.get("schedule_ready", not event.get("needs_review")):
        activity = _display_datetime(event["start_at"])
        if event.get("end_at"):
            activity += " 至 " + _display_datetime(event["end_at"])
    else:
        activity = (" ".join(_text(event.get("activity_time_text")).split())[:400] + "（待核实）"
                    if event.get("activity_time_text") else "未注明（待核实）")
    if cancelled:
        activity = "已取消；原定 " + activity if event.get("start_at") else "已取消"
    return subject, f"讲座摘要：{summary}\n报名截止时间：{deadline}\n讲座活动时间：{activity}"


def format_notification_html(event: dict, reason: str) -> str | None:
    """Render the same source, preserving paragraphs, spacing and literal links."""
    _validate_notification(event, reason)
    if event.get("category") != "lecture_notice":
        return None
    fields = _notice_fields(event)
    source = _source_html(fields["body"])
    extracted_html = ""
    if notification_format(event) == "llm_summary":
        blocks = []
        for label, value in _extracted_notice_fields(event):
            content = escape(value, quote=True)
            size = "19px" if label == "讲座主题" else "16px"
            weight = "600" if label == "讲座主题" else "400"
            if label == "报名链接":
                try:
                    parsed = urlsplit(value)
                    valid_link = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
                except ValueError:
                    valid_link = False
                if valid_link:
                    content = ('<a href="' + escape(value, quote=True) + '" target="_blank" rel="noopener noreferrer" '
                        'style="display:inline-block;padding:11px 18px;border-radius:6px;background:#2563eb;'
                        'color:#ffffff;text-decoration:none;font-size:16px;font-weight:600;line-height:1.5">打开报名页面 →</a>')
            blocks.append('<tr><td style="padding:13px 0;border-bottom:1px solid #e5e7eb">'
                '<div style="margin:0 0 6px;color:#6b7280;font-size:13px;line-height:1.5">' + escape(label) + '</div>'
                '<div style="color:#17212f;font-size:' + size + ';font-weight:' + weight + ';line-height:1.75;'
                'white-space:pre-wrap;word-break:normal;overflow-wrap:break-word;word-wrap:break-word">'
                + content + '</div></td></tr>')
        extracted_html = ('<div id="llm-extraction" style="margin-bottom:24px">'
            '<h2 style="margin:0 0 8px;font-size:20px;color:#17212f">LLM 提取信息</h2>'
            '<p style="margin:0 0 12px;font-size:13px;color:#6b7280">自动提取，请核对下方原文；未注明的信息不作推测。</p>'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;table-layout:fixed">'
            + ''.join(blocks) + '</table></div>')
    note = ('<p style="margin:20px 0 0;padding:10px 12px;background:#fff8e6;'
            'color:#76530b;border-radius:6px;font-size:14px;line-height:1.7">提示：' +
            escape(fields["note"], quote=True) + '</p>') if fields["note"] else ""
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;color:#17212f;-webkit-text-size-adjust:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f8"><tr><td align="center" style="padding:12px 6px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#ffffff;border:1px solid #e5e7eb;border-radius:10px"><tr><td style="padding:20px 16px">
{extracted_html}
<h1 style="margin:0 0 16px;font-size:14px;line-height:1.5;font-weight:500;color:#6b7280">讲座消息原文</h1>
<div id="source-text" style="white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font-size:16px;line-height:1.85">{source}</div>{note}
</td></tr></table></td></tr></table></body></html>'''
