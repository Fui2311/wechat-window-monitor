"""Conservative, local rules for extracting lecture announcements.

This module does not open attachments, follow URLs, or contact a model.  Every
extracted field carries a source snippet; unresolved facts stay unresolved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any


SHANGHAI = timezone(timedelta(hours=8))
MAX_FORWARD_DEPTH = 4
MAX_MESSAGE_NODES = 100
MAX_TEXT_LENGTH = 50_000
ANALYSIS_POLICY = "manual-signup-v1"

_LECTURE = re.compile(r"讲座|学术报告|学术讲堂|学术沙龙|报告题目|报告主题|学术讲演")
_UPDATE = re.compile(r"取消|改期|延期|变更|更改|调整|改为|改至|推迟|提前至")
_CANCEL = re.compile(
    r"(?:讲座|报告|活动)(?:现已|现|已经|将|已|决定|因[^\n，。；;]{0,16})?\s*(?:取消|停止举办)"
    r"|(?:取消|停止举办)[^\n，。；;]{0,30}(?:讲座|报告|活动)"
)
_DATE = re.compile(
    r"(?<!\d)(?<!\d:)(?<!\d：)(?:(?P<year>20\d{2})\s*(?:年|[-/.])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:月|[-/.])\s*(?P<day>\d{1,2})(?!\d)\s*日?"
)
_RELATIVE = re.compile(r"今天|今日|明天|明日|后天")
_PERIOD = r"(?:上午|下午|晚上|中午|凌晨|早上|傍晚)"
_TIME = re.compile(
    rf"(?P<period>{_PERIOD})?\s*(?P<hour>\d{{1,2}})\s*[:：]\s*(?P<minute>\d{{2}})"
)
_RANGE_GAP = re.compile(r"^\s*[-－—–‑‐−~～至到]+\s*$")
_DEADLINE = re.compile(r"报名(?:截止|截至|时间|期限)|(?:截止|截至)(?:时间|日期)|(?:报名|预约|登记)[^\n。；;]{0,40}(?:截止|截至)|(?:截止|截至)[^\n。；;]{0,30}(?:报名|预约|登记)|(?:请)?于[^\n。；;]{0,60}(?:前|之前)(?:完成)?(?:报名|预约|登记)")
_EVENT_TIME_LINE = re.compile(r"(?:讲座时间|报告时间|活动时间|举办时间|时间)\s*[:：]")
_WEEKDAY = re.compile(r"(?:星期|周|礼拜)([一二三四五六日天])")
_TITLE_LABEL = re.compile(r"(?:讲座主题|讲座题目|报告主题|报告题目|主题|题目)\s*[:：]\s*(.+)")
_SUMMARY_LABEL = re.compile(r"(?:讲座摘要|报告摘要|内容摘要|讲座简介|报告简介|讲座内容|报告内容|内容简介|摘要|简介)\s*[:：]\s*(.*)")
_FIELD_BOUNDARY = re.compile(
    r"[\U0001F000-\U0001FAFF\u2300-\u27FF\ufe0f]*[ \t]*"
    r"(?:讲座主题|讲座题目|报告主题|报告题目|主题|题目|"
    r"主讲嘉宾|主讲人|主讲专家|主讲|报告人|演讲者|嘉宾|"
    r"讲座时间|报告时间|活动时间|举办时间|时间|"
    r"讲座地点|报告地点|活动地点|地点|会场|温馨提示|报名截止时间|报名截至时间|报名截止|报名截至|报名时间|报名期限|"
    r"讲座摘要|报告摘要|内容摘要|讲座简介|报告简介|讲座内容|报告内容|内容简介|摘要|简介)\s*[:：]"
)


def _field_lines(text: str) -> str:
    """Restore label boundaries when accessibility APIs flatten line breaks."""
    return _FIELD_BOUNDARY.sub(lambda match: "\n" + match.group(0).lstrip(), text)


def _change_text(text: str) -> str:
    # A registration policy is not a cancellation of the lecture itself.
    text = re.sub(r"取消\s*报名", "", text)
    return re.sub(r"(?:不|未|不会|不能|不可|不得|禁止|没有|无需|并非)(?:再|会|予以)?\s*(?:取消|改期|延期|变更|更改|调整)", "", text)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def _base_time(value: Any) -> tuple[datetime | None, str | None]:
    if not isinstance(value, str) or not value:
        return None, "缺少消息发送时间，无法确定相对日期和年份"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "消息发送时间格式无法识别"
    if parsed.tzinfo is None:
        return None, "消息发送时间缺少时区，无法可靠解析相对日期"
    return parsed.astimezone(SHANGHAI), None


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _dates(text: str, base: datetime | None) -> tuple[list[tuple[Any, str]], list[str]]:
    dates: list[tuple[Any, str]] = []
    reasons: list[str] = []
    for match in _DATE.finditer(text):
        year = int(match["year"]) if match["year"] else (base.year if base else None)
        if year is None:
            reasons.append("日期缺少年份，且没有可靠的消息发送时间")
            continue
        try:
            day = datetime(year, int(match["month"]), int(match["day"]), tzinfo=SHANGHAI).date()
        except ValueError:
            reasons.append(f"日期无效：{match.group(0).strip()}")
            continue
        dates.append((day, match.group(0).strip()))
    for match in _RELATIVE.finditer(text):
        if base is None:
            reasons.append("相对日期需要带时区的消息发送时间")
            continue
        offset = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}[match.group(0)]
        dates.append(((base + timedelta(days=offset)).date(), match.group(0)))
    return dates, reasons


def _clock(match: re.Match[str], inherited_period: str | None = None) -> tuple[int, int] | None:
    hour, minute = int(match["hour"]), int(match["minute"])
    period = match["period"] or inherited_period
    if minute > 59 or hour > 23:
        return None
    if period:
        if hour > 12:
            # "下午14:30" is redundant, but unambiguous.
            if period in ("上午", "凌晨", "早上"):
                return None
        elif period in ("下午", "晚上", "傍晚") and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        elif period == "凌晨" and hour == 12:
            hour = 0
    return hour, minute


def _times(text: str) -> tuple[list[tuple[tuple[int, int], tuple[int, int] | None, str]], list[str]]:
    matches = list(_TIME.finditer(text))
    result: list[tuple[tuple[int, int], tuple[int, int] | None, str]] = []
    reasons: list[str] = []
    index = 0
    while index < len(matches):
        first = matches[index]
        start = _clock(first)
        if start is None:
            reasons.append(f"时间无效：{first.group(0).strip()}")
            index += 1
            continue
        end = None
        snippet = first.group(0).strip()
        if index + 1 < len(matches):
            second = matches[index + 1]
            if _RANGE_GAP.fullmatch(text[first.end():second.start()]):
                end = _clock(second, first["period"])
                snippet = text[first.start():second.end()].strip()
                if end is None:
                    reasons.append(f"结束时间无效：{second.group(0).strip()}")
                index += 1
        result.append((start, end, snippet))
        index += 1
    return result, reasons


def time_excerpt(text: str) -> str:
    """Retain only time evidence, without copying attendance or signup rules."""
    tokens = sorted((match.start(), match.end(), match.group(0).strip())
                    for pattern in (_DATE, _RELATIVE, _TIME, _WEEKDAY)
                    for match in pattern.finditer(text))
    return " / ".join(value for _, _, value in tokens)[:400]


def lecture_summary(text: str, title: str | None) -> tuple[str, str | None]:
    """Extract up to two content sentences; never add facts or signup status."""
    details = []
    in_content = False
    for line in _field_lines(text).splitlines():
        labelled = _SUMMARY_LABEL.search(line)
        value = labelled.group(1) if labelled else line.strip()
        if labelled:
            in_content = True
        elif not value or _FIELD_BOUNDARY.search(line):
            in_content = False
        if not labelled and not in_content and not re.match(r"^(?:本次|本场|此次)(?:讲座|报告)(?:将|主要|围绕|聚焦|介绍|探讨|分享)", value):
            continue
        for sentence in re.split(r"(?<=[。！？])", value):
            sentence = sentence.strip()
            if not sentence or re.search(r"签到|签退|到场|到达|名单|报名|盖章|院章|主讲|地点|截止", sentence):
                continue
            details.append(sentence)
    details = _unique(details)[:2]
    heading = f"讲座《{title}》" if title else "讲座通知（主题待核实）"
    detail = "".join(details)
    if len(detail) > 220:
        detail = detail[:220] + "…"
    return heading + (f"：{detail}" if detail else "。"), detail or None


def _clean(value: str) -> str:
    value = value.strip().rstrip("。；;")
    for left, right in (("《", "》"), ("「", "」"), ("【", "】"), ('"', '"'), ("“", "”")):
        if value.startswith(left) and value.endswith(right):
            value = value[len(left):-len(right)].strip()
    return value


def _title(text: str) -> tuple[str | None, str | None]:
    label = _TITLE_LABEL.search(text)
    if label:
        value = label.group(1).splitlines()[0].strip()
        return _clean(value), value
    quoted = re.search(r"《([^》\n]{2,150})》", text)
    if quoted:
        return quoted.group(1).strip(), quoted.group(0)
    # An unquoted heading is acceptable only when it is an announcement heading.
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^(?:[【\[（(]?讲座(?:通知|预告|提醒)?[】\]）)]?|学术报告(?:通知|预告)?)\s*[:：]", line):
            return _clean(line.split("：", 1)[-1].split(":", 1)[-1]), line
    return None, None


def _candidate(message: dict[str, Any], text: str, *, lead: str | None = None) -> dict[str, Any] | None:
    analysis_text = _field_lines(text)
    title, title_evidence = _title(analysis_text)
    raw_attachments = message.get("attachments", [])
    attachments = [item for item in raw_attachments if isinstance(item, dict)] if isinstance(raw_attachments, list) else []
    attachment_lecture = any(_LECTURE.search(str(item.get("name", ""))) for item in attachments)
    structured = bool(re.search(r"(?:主讲|报告人|讲座时间|报告时间|讲座地点|报告地点)\s*[:：]", analysis_text))
    change_text = _change_text(analysis_text)
    is_update = bool(_UPDATE.search(change_text) and (_LECTURE.search(text) or title))
    has_lecture = bool(_LECTURE.search(text)) or structured or attachment_lecture
    # Ordinary conversation about lectures does not establish an event.
    announcement = bool(title or structured or re.search(r"(?:讲座|学术报告)(?:通知|预告|提醒)", text))
    if lead is None and attachment_lecture and not (announcement or is_update):
        lead = "附件含讲座线索，但缺少可读取的通知正文，需人工核对"
    if lead is None and not (has_lecture and (announcement or is_update)):
        return None

    base, _ = _base_time(message.get("sent_at"))
    reasons: list[str] = [lead] if lead else []
    # A full absolute date needs no original sending timestamp. Relative dates
    # and missing years still fail conservatively in _dates().
    evidence: dict[str, str] = {}
    if title_evidence:
        evidence["title"] = title_evidence
    if not title:
        reasons.append("未能确定讲座主题")
    candidate: dict[str, Any] = {
        "title": title,
        "summary": lecture_summary(analysis_text, title)[0],
        "analysis_policy": ANALYSIS_POLICY,
        "category": "lecture_update" if is_update else "lecture",
        "start_at": None,
        "end_at": None,
        "deadline_at": None,
        "deadline_text": "",
        "activity_time_text": "",
        "confidence": 0.0,
        "needs_review": True,
        "schedule_ready": False,
        "review_reasons": [],
        "evidence": evidence,
        "source_message_id": message.get("id"),
        "source_group": message.get("group"),
        "source_sender": message.get("sender"),
        "source_sent_at": message.get("sent_at"),
        "source_text": text,
        "status": "cancelled" if _CANCEL.search(change_text) else "active",
    }
    # A forwarding card is a pointer, never a body to extract scheduling from.
    if lead:
        if text:
            evidence["forward"] = text
        names = [str(item.get("name", "")).strip() for item in attachments if item.get("name")]
        if names:
            evidence["attachments"] = "\n".join(names)
        candidate["summary"] = f"{candidate['summary']}正文未读取，待核实。"
        candidate["review_reasons"] = _unique(reasons)
        candidate["confidence"] = 0.15
        return candidate

    _, summary_detail = lecture_summary(analysis_text, title)
    if summary_detail:
        evidence["summary"] = summary_detail

    event_lines: list[str] = []
    deadline_lines: list[str] = []
    for line in re.split(r"\n|(?<=[，。；;])(?=\s*(?:请于|报名|预约|登记|截止|截至))", analysis_text):
        # A prohibition on cancelling a registration and a check-in cutoff
        # are not registration deadlines.
        if _DEADLINE.search(line) and not re.search(r"取消报名|签到|签退", line):
            deadline_lines.append(line)
        elif not re.search(r"签到|签退|提前.{0,12}(?:到场|到达)|报名时间|报名期限", line):
            event_lines.append(line)
    event_text = "\n".join(event_lines)
    labelled_time_lines = [line for line in event_lines if _EVENT_TIME_LINE.search(line)]
    # The signature date and registration-policy dates are not event dates.
    # Keep every labelled time line so competing schedules remain ambiguous.
    schedule_text = "\n".join(labelled_time_lines) if labelled_time_lines else event_text
    candidate["activity_time_text"] = time_excerpt(schedule_text)
    found_dates, date_reasons = _dates(schedule_text, base)
    found_times, time_reasons = _times(schedule_text)
    reasons.extend(date_reasons + time_reasons)
    schedule_issues = bool(date_reasons or time_reasons)
    unique_dates = _unique([day for day, _ in found_dates])
    unique_times = _unique([(start, end) for start, end, _ in found_times])
    event_day = unique_dates[0] if len(unique_dates) == 1 else None
    time_pair = unique_times[0] if len(unique_times) == 1 else None
    if len(unique_dates) > 1:
        reasons.append("出现多个活动日期，无法确定当前安排")
        schedule_issues = True
    elif not unique_dates and candidate["status"] != "cancelled":
        reasons.append("缺少可确定的活动日期")
    if len(unique_times) > 1:
        reasons.append("出现多个活动时间，无法确定当前安排")
        schedule_issues = True
    elif not unique_times and candidate["status"] != "cancelled":
        reasons.append("缺少可确定的活动开始时间")
    if event_day and time_pair:
        start_clock, end_clock = time_pair
        start = datetime.combine(event_day, datetime.min.time(), SHANGHAI).replace(hour=start_clock[0], minute=start_clock[1])
        candidate["start_at"] = _iso(start)
        evidence["start_at"] = f"{found_dates[0][1]} {found_times[0][2]}"
        if end_clock:
            end = start.replace(hour=end_clock[0], minute=end_clock[1])
            if end <= start:
                reasons.append("结束时间不晚于开始时间，需核对是否跨天")
                schedule_issues = True
            else:
                candidate["end_at"] = _iso(end)
                evidence["end_at"] = found_times[0][2]
        for weekday in _WEEKDAY.findall(schedule_text):
            expected = "一二三四五六日"[event_day.weekday()]
            if ("日" if weekday == "天" else weekday) != expected:
                reasons.append("日期与文字中的星期不一致")
                schedule_issues = True

    deadline_text = "\n".join(deadline_lines)
    candidate["deadline_text"] = time_excerpt(deadline_text) or ("截止日期和时刻未注明" if deadline_text else "")
    if deadline_text:
        deadline_dates, deadline_date_reasons = _dates(deadline_text, base)
        deadline_times, deadline_time_reasons = _times(deadline_text)
        reasons.extend(deadline_date_reasons + deadline_time_reasons)
        days = _unique([day for day, _ in deadline_dates])
        clocks = _unique([start for start, _, _ in deadline_times])
        # A same-day registration window states its cutoff at the range end.
        if len(deadline_times) == 1 and deadline_times[0][1] and "报名时间" in deadline_text:
            first, last, _ = deadline_times[0]
            if last > first:
                clocks = [last]
            else:
                deadline_time_reasons.append("报名时间范围结束不晚于开始")
        if len(days) == 1 and len(clocks) == 1 and not (deadline_date_reasons or deadline_time_reasons):
            deadline = datetime.combine(days[0], datetime.min.time(), SHANGHAI).replace(hour=clocks[0][0], minute=clocks[0][1])
            candidate["deadline_at"] = _iso(deadline)
            evidence["deadline_at"] = deadline_text
        else:
            reasons.append("报名截止日期或时刻不完整或有歧义")

    if candidate["status"] == "cancelled":
        evidence["status"] = next((line.strip() for line in analysis_text.splitlines() if _CANCEL.search(_change_text(line))), text)
    reasons = _unique(reasons)
    score = 0.3 + (0.25 if title else 0) + (0.3 if candidate["start_at"] else 0) + (0.1 if candidate["deadline_at"] else 0)
    candidate["confidence"] = round(max(0.05, min(0.95, score - min(0.4, 0.08 * len(reasons)))), 2)
    candidate["review_reasons"] = reasons
    candidate["needs_review"] = bool(reasons)
    candidate["schedule_ready"] = bool(
        title and not schedule_issues and (
            candidate["status"] == "cancelled"
            or candidate["start_at"]
        )
    )
    return candidate


def analyze_message(message: dict) -> list[dict]:
    """Extract candidates from one message and its bounded forwarding tree.

    Children inherit only their group and a derived identity.  Forwarding time
    and forwarder identity are not evidence of the original sending time or
    sender.  Date calculations always use
    the message's sending date in UTC+08:00, never the machine's current date.
    Missing or inaccessible forward bodies produce a review-only candidate.
    """
    if not isinstance(message, dict):
        return []
    results: list[dict] = []
    seen: set[int] = set()
    count = 0

    def visit(node: dict, inherited: dict, depth: int) -> list[dict]:
        nonlocal count
        merged = {"id": inherited.get("id"), "group": inherited.get("group"), "sender": None, "sent_at": None}
        merged.update(node)
        if not merged.get("id"):
            merged["id"] = inherited.get("id")
        raw_text = merged.get("text", "")
        text = raw_text if isinstance(raw_text, str) else ""
        if id(node) in seen:
            item = _candidate(merged, text, lead="转发结构存在循环，正文未继续解析")
            return [item] if item else []
        if depth > MAX_FORWARD_DEPTH or count >= MAX_MESSAGE_NODES:
            item = _candidate(merged, text[:MAX_TEXT_LENGTH], lead="转发层数或条数超过解析上限，需人工展开核对")
            return [item] if item else []
        seen.add(id(node))
        count += 1
        if len(text) > MAX_TEXT_LENGTH:
            item = _candidate(merged, text[:MAX_TEXT_LENGTH], lead="消息正文超过解析长度上限，需人工核对完整内容")
            return [item] if item else []
        children = merged.get("children", [])
        valid_children = [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []
        found: list[dict] = []
        if merged.get("kind") == "forward":
            for child_index, child in enumerate(valid_children):
                if count >= MAX_MESSAGE_NODES:
                    item = _candidate(merged, text, lead="转发条数超过解析上限，部分正文未解析")
                    if item:
                        found.append(item)
                    break
                child_metadata = {"group": merged.get("group"), "id": f"{merged.get('id') or 'message'}/child-{child_index + 1}"}
                found.extend(visit(child, child_metadata, depth + 1))
            if not valid_children:
                item = _candidate(merged, text, lead="转发卡片没有可读取的子消息正文，需人工展开核对")
                if item:
                    found.append(item)
            return found
        item = _candidate(merged, text)
        if item:
            found.append(item)
        for child_index, child in enumerate(valid_children):
            if count >= MAX_MESSAGE_NODES:
                break
            child_metadata = {"group": merged.get("group"), "id": f"{merged.get('id') or 'message'}/child-{child_index + 1}"}
            found.extend(visit(child, child_metadata, depth + 1))
        return found

    results.extend(visit(message, {}, 0))
    return results
