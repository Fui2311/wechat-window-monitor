"""Format event notifications and send one email over verified TLS.

Queueing, deduplication, retry policy and dry-run handling belong to the caller.
This module never fetches or attaches group files.
"""

from __future__ import annotations

import math
import os
import re
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any


class EmailConfigurationError(ValueError):
    """A notification or SMTP configuration cannot be used safely."""


class EmailNotificationError(RuntimeError):
    """A single SMTP attempt failed; the caller may apply its retry policy."""


_REQUIRED_KEYS = frozenset(
    {
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_user_env",
        "smtp_password_env",
        "from_env",
        "to_env",
    }
)
_ALLOWED_KEYS = _REQUIRED_KEYS | {"smtp_timeout"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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


def _items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [item for entry in value if (item := _text(entry))]
    return [item] if (item := _text(value)) else []


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


def format_notification(event: dict, reason: str) -> tuple[str, str]:
    """Return a Chinese subject/body without inferring personal signup status."""
    if not isinstance(event, dict):
        raise EmailConfigurationError("event 必须是对象。")
    if reason not in _REASONS:
        raise EmailConfigurationError("未知通知原因；允许 new/update/cancelled/reminder/review。")

    title = _text(event.get("title")) or "未识别标题"
    # Subjects must remain one line even when source-derived titles contain newlines.
    subject_title = " ".join(title.split())[:180]
    subject = f"[微信群通知·{_REASONS[reason]}] {subject_title}"
    status = _text(event.get("status"))
    if reason == "cancelled" or status == "cancelled":
        status_label = "已取消，请核对原通知"
    elif status in {"active", "scheduled", "confirmed"}:
        status_label = "已识别活动，具体安排以原通知为准"
    else:
        status_label = status or "待核实"

    lines = [
        f"通知类型：{_REASONS[reason]}",
        f"主题：{title}",
        f"状态：{status_label}",
        f"开始：{_display_datetime(event.get('start_at'))}",
    ]
    for key, label in (
        ("end_at", "结束"),
        ("arrival_at", "建议到达"),
        ("deadline_at", "截止"),
    ):
        if event.get(key):
            lines.append(f"{label}：{_display_datetime(event[key])}")
    lines.append(f"地点：{_text(event.get('location')) or '未识别，请核对原文'}")
    if speakers := _items(event.get("speakers")):
        lines.append(f"主讲：{'、'.join(speakers)}")
    if requirements := _items(event.get("requirements")):
        lines.extend(["", "参与要求：", *(f"- {item}" for item in requirements)])

    review_reasons = _items(event.get("review_reasons"))
    if not event.get("start_at"):
        review_reasons.append("未识别活动开始时间。")
    if not event.get("location"):
        review_reasons.append("未识别活动地点。")
    if event.get("needs_review") and not review_reasons:
        review_reasons.append("解析结果需要人工核实。")
    if reason == "review" and not review_reasons:
        review_reasons.append("本条通知被标记为待核实。")
    lines.extend(["", "需要核实："])
    if review_reasons:
        lines.extend(f"- {item}" for item in dict.fromkeys(review_reasons))
    else:
        lines.append("- 暂无自动标记；具体安排仍请核对原通知。")
    lines.append("- 无法确认你是否报名成功或在名单内；请核对群内原通知和名单附件。")

    lines.extend(
        [
            "",
            f"来源群：{_text(event.get('source_group')) or '未提供'}",
            f"发送者：{_text(event.get('source_sender')) or '未提供'}",
        ]
    )
    if event.get("version") is not None:
        lines.append(f"记录版本：{event['version']}")
    source = _text(event.get("source_text"))
    if source:
        evidence = source[:1000]
        if len(source) > 1000:
            evidence += "\n（原文较长，以上为截取摘要，请回群核对完整内容。）"
        lines.extend(["", "来源原文摘录：", evidence])
    else:
        lines.extend(["", "来源原文未提供，以上内容需要回群核对。"])
    return subject, "\n".join(lines)


def _env_value(config: dict, key: str) -> str:
    name = config[key]
    if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
        raise EmailConfigurationError(f"{key} 必须是有效的环境变量名。")
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise EmailConfigurationError(f"{key} 指定的环境变量未设置或为空。")
    return value


def _mailboxes(raw: str, label: str, *, multiple: bool) -> list[str]:
    if "\r" in raw or "\n" in raw:
        raise EmailConfigurationError(f"{label} 包含非法换行。")
    addresses = [part.strip() for part in raw.split(",")]
    if not multiple and len(addresses) != 1:
        raise EmailConfigurationError(f"{label} 只允许一个邮箱地址。")
    if not all(addresses):
        raise EmailConfigurationError(f"{label} 包含空邮箱地址。")
    result = []
    for address in addresses:
        try:
            parsed = Address(addr_spec=address)
        except (HeaderParseError, ValueError, IndexError):
            raise EmailConfigurationError(f"{label} 包含无效邮箱地址。") from None
        if (
            not parsed.username
            or not parsed.domain
            or any(character.isspace() for character in address)
            or any(character in address for character in "<>;()")
            or not address.isascii()
        ):
            raise EmailConfigurationError(f"{label} 必须使用纯邮箱地址，多个收件人用逗号分隔。")
        result.append(address)
    return list(dict.fromkeys(result))


def send_email(subject: str, body: str, config: dict) -> None:
    """Send one plain-text UTF-8 email; never fall back to unencrypted SMTP.

    Credentials and email addresses are read exclusively from named environment
    variables. Failure messages intentionally omit server responses and secrets.
    A partial recipient refusal is a failure for the caller to inspect/retry.
    """
    if not isinstance(config, dict):
        raise EmailConfigurationError("邮箱配置必须是对象。")
    if any(not isinstance(key, str) for key in config):
        raise EmailConfigurationError("邮箱配置键必须是字符串。")
    unknown = set(config) - _ALLOWED_KEYS
    if unknown:
        # Do not echo unknown keys: an accidental secret could itself be a key.
        raise EmailConfigurationError("邮箱配置包含未知字段；请参照 docs/email.md。")
    missing = _REQUIRED_KEYS - set(config)
    if missing:
        raise EmailConfigurationError(f"邮箱配置缺少字段：{', '.join(sorted(missing))}。")
    host = config["smtp_host"]
    if (
        not isinstance(host, str)
        or not host
        or any(character.isspace() for character in host)
        or any(character in host for character in "/\\@")
    ):
        raise EmailConfigurationError("smtp_host 必须是有效的 SMTP 主机名。")
    port = config["smtp_port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise EmailConfigurationError("smtp_port 必须是 1 至 65535 的整数。")
    security = config["smtp_security"]
    if security not in ("ssl", "starttls"):
        raise EmailConfigurationError("smtp_security 只允许 ssl 或 starttls，不允许明文连接。")
    timeout = config.get("smtp_timeout", 15)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise EmailConfigurationError("smtp_timeout 必须是大于零的有限秒数。")
    if not isinstance(subject, str) or "\r" in subject or "\n" in subject:
        raise EmailConfigurationError("邮件主题必须是单行字符串。")
    if not isinstance(body, str):
        raise EmailConfigurationError("邮件正文必须是字符串。")

    # Finish all validation before creating a network connection.
    user = _env_value(config, "smtp_user_env")
    password = _env_value(config, "smtp_password_env")
    sender = _mailboxes(_env_value(config, "from_env"), "from_env", multiple=False)[0]
    recipients = _mailboxes(_env_value(config, "to_env"), "to_env", multiple=True)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body, charset="utf-8")
    context = ssl.create_default_context()
    try:
        if security == "ssl":
            connection = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            connection = smtplib.SMTP(host, port, timeout=timeout)
        with connection as smtp:
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(user, password)
            refused = smtp.send_message(message, from_addr=sender, to_addrs=recipients)
            if refused:
                raise EmailNotificationError("邮件部分收件人被拒绝；请核对收件地址及 SMTP 服务。")
    except EmailNotificationError:
        raise
    except (smtplib.SMTPException, OSError, ValueError):
        # SMTP exceptions may include provider responses or credentials. Never
        # interpolate them or retain their traceback as a chained exception.
        raise EmailNotificationError("邮件发送失败；请检查 SMTP 服务、TLS、网络和授权码。") from None
