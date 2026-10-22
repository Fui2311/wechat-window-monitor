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
from html import escape
from typing import Any
from urllib.parse import urlsplit


from .presentation.notifications import (
    EmailConfigurationError, EmailNotificationError, format_notification,
    format_notification_html, notification_format,
)

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


def send_email(subject: str, body: str, config: dict, *, html_body: str | None = None) -> None:
    """Send UTF-8 text with an optional HTML alternative over verified TLS.

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
    if html_body is not None and not isinstance(html_body, str):
        raise EmailConfigurationError("HTML 邮件正文必须是字符串。")

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
    if html_body is not None:
        message.add_alternative(html_body, subtype="html", charset="utf-8")
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
