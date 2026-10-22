"""Deterministic message validation and identity; no I/O."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 2026-10-15T13:00:00+08:00")
    return parsed


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def stable_message(value):
    """Observation metadata never changes the identity of nested evidence."""
    if isinstance(value, dict):
        return {k: stable_message(v) for k, v in value.items() if k not in ("observed_at", "source_meta")}
    if isinstance(value, list):
        return [stable_message(v) for v in value]
    return value


def title_key(value: str) -> str:
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", value)).lower()


def schedule_ready(event: dict) -> bool:
    return bool(event.get("schedule_ready", not event.get("needs_review")))


def validate_message(message: dict, expected_group: str, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 6 or budget[0] > 200:
        raise ValueError("转发记录超过6层或200条；请分批导入")
    if not isinstance(message, dict):
        raise ValueError("消息必须是JSON对象")
    if message.get("group", expected_group) != expected_group:
        raise ValueError("消息群名不在目标白名单")
    for field in ("text", "sender", "sent_at", "id"):
        value = message.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 30000):
            raise ValueError(f"消息字段{field}必须是长度不超过30000的文本")
    if message.get("sent_at"):
        parse_time(message["sent_at"])
    for field in ("children", "attachments"):
        if not isinstance(message.get(field, []), list):
            raise ValueError(f"{field}必须是列表")
    if len(message.get("attachments", [])) > 100:
        raise ValueError("单条消息附件数超过100")
    for attachment in message.get("attachments", []):
        if not isinstance(attachment, dict) or any(
            not isinstance(attachment.get(k, ""), str) or len(attachment.get(k, "")) > 30000
            for k in ("text", "name", "kind")
        ):
            raise ValueError("附件元数据无效或过长")
    for child in message.get("children", []):
        validate_message(child, expected_group, depth + 1, budget)
