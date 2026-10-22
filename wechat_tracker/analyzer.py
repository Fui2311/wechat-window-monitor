"""Find lecture-related messages and copy useful signup details locally.

Relevance is enough to notify. Optional time extraction never decides whether
an event is cancelled, whether a user can attend, or whether to send a notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any


SHANGHAI = timezone(timedelta(hours=8))
MAX_FORWARD_DEPTH = 4
MAX_MESSAGE_NODES = 100
MAX_TEXT_LENGTH = 50_000
from .domain.policy import ANALYSIS_POLICY

_LECTURE = re.compile(
    r"讲座|学术报告|学术讲堂|学术沙龙|报告题目|报告主题|学术讲演|学术论坛|学术研讨会|"
    r"勷勤论坛|(?:学术|科研|研途)(?:经验)?分享(?:交流)?会"
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
_SIGNUP_TIME = re.compile(r"(?:报名|预约|登记)(?:开始时间|开放时间|开启时间|开始|开放|开启|时间|时段)\s*[:：]?\s*")
_SIGNUP_OPEN = re.compile(r"(?:报名|预约|登记)[^\n，。；;]{0,50}(?:开始|开放|开启)|(?:开始|开放|开启)(?:报名|预约|登记)")
_SIGNUP_CUTOFF = re.compile(r"截止|截至|结束|关闭|停止|取消|签到|签退")
_SIGNUP_LINK = re.compile(r"(?:报名|预约|登记|问卷)(?:链接|地址|入口|网址|方式)\s*[:：]?\s*")
_URL = re.compile(r"https?://[^\s<>\"'，。；！？、（）【】《》\u200b]+", re.IGNORECASE)
_EVENT_TIME_LINE = re.compile(r"(?:讲座时间|报告时间|活动时间|举办时间|时间)\s*[:：]")
_WEEKDAY = re.compile(r"(?:星期|周|礼拜)([一二三四五六日天])")
_TITLE_LABEL = re.compile(r"(?:讲座主题|讲座题目|报告主题|报告题目|活动主题|主题|题目)\s*[:：]\s*(.+)")
_SUMMARY_LABEL = re.compile(r"(?<![\u4e00-\u9fff])(?:讲座摘要|报告摘要|内容摘要|讲座简介|报告简介|讲座内容|报告内容|内容简介|摘要|简介)\s*[:：]\s*(.*)")
_FIELD_BOUNDARY = re.compile(
    r"[\U0001F000-\U0001FAFF\u2300-\u27FF\ufe0f]*[ \t]*"
    r"(?:讲座主题|讲座题目|报告主题|报告题目|活动主题|主题|题目|"
    r"主讲人简介|主讲嘉宾简介|嘉宾简介|报告人简介|主讲嘉宾|活动嘉宾|主讲人|主讲专家|主讲|报告人|演讲者|嘉宾|"
    r"讲座时间|报告时间|活动时间|举办时间|时间|"
    r"讲座地点|报告地点|活动地点|举办地点|举办校区|活动校区|所在校区|校区|地点|会场|讲座形式|活动对象|报名对象|面向对象|活动名额|主办单位|承办单位|温馨提示|报名截止时间|报名截至时间|报名截止|报名截至|"
    r"(?:报名|预约|登记)(?:开始时间|开放时间|开启时间|开始|开放|开启|时间|时段)|报名期限|报名链接|预约链接|报名入口|报名方式|"
    r"讲座摘要|报告摘要|内容摘要|讲座简介|报告简介|讲座内容|报告内容|内容简介|(?<![\u4e00-\u9fff])(?:摘要|简介))\s*[:：]"
)


def _field_lines(text: str) -> str:
    """Restore label boundaries when accessibility APIs flatten line breaks."""
    return _FIELD_BOUNDARY.sub(lambda match: "\n" + match.group(0).lstrip(), text)


_VENUE_FIELD = re.compile(r"(?:讲座地点|报告地点|活动地点|举办地点|举办校区|活动校区|所在校区|校区|地点|会场)\s*[:：]\s*")
_NON_VENUE_FIELD = re.compile(
    r"(?:主讲人简介|主讲嘉宾简介|嘉宾简介|报告人简介|主讲嘉宾|活动嘉宾|主讲人|主讲专家|"
    r"主讲|报告人|演讲者|嘉宾|活动对象|报名对象|面向对象|主办单位|承办单位)\s*[:：]"
)
_KNOWN_CAMPUS = re.compile(r"(石牌|大学城|佛山|南海)(校区|校园)?")
_OTHER_CAMPUS = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·]{1,20}校(?:区|园)")


def campus_allowed(message: dict, text: str) -> bool:
    """Keep Shipai venues and unknown campuses, using captured evidence only.

    A venue field takes priority over incidental campus names. Multiple venues
    are eligible when Shipai is one of them. Building names never imply a campus.
    """
    venues, narrative = [], []
    zone = "narrative"
    for line in _field_lines(text).splitlines():
        if not line.strip():
            if zone == "venue":
                zone = "narrative"
            continue
        venue = _VENUE_FIELD.search(line)
        if venue:
            value = line[venue.end():]
            venues.append(value)
            zone = "narrative" if value.strip() else "venue"
            continue
        if _NON_VENUE_FIELD.search(line):
            zone = "ignore"
            continue
        if _FIELD_BOUNDARY.search(line):
            zone = "narrative"
        if zone == "venue":
            venues.append(line)
            zone = "narrative"
        elif zone != "ignore":
            narrative.append(line)

    def campuses(value, *, venue=False):
        compact = re.sub(r"\s+", "", value)
        if venue:
            # A changed venue is governed by its destination, not its old site.
            compact = re.split(r"(?:改为|改至|改到|调整为|调整至|变更为|变更至|迁至|转至)", compact)[-1]
        compact = re.sub(r"(?:不在|不是|并非|非)石牌(?:校区|校园)?", "", compact)
        known = [match[1] for match in _KNOWN_CAMPUS.finditer(compact)
                 if venue or match[2] or re.search(r"(?:在|于|到|赴|前往|地点为|地点是)$", compact[:match.start()])]
        # Topic concepts and collective audiences are not named campuses.
        geographic = re.sub(r"(?:智慧|数字|智能|平安|美丽|和谐|绿色|文明|书香)校园|(?:各|全|本)校区", "", compact)
        other = _OTHER_CAMPUS.findall(geographic)
        return known, other

    venue_campuses = [campuses(value, venue=True) for value in venues]
    if any(known or other for known, other in venue_campuses):
        return any("石牌" in known for known, _ in venue_campuses)
    raw_attachments = message.get("attachments", [])
    if isinstance(raw_attachments, list):
        narrative.extend(str(item.get("name", "")) for item in raw_attachments if isinstance(item, dict))
    remaining = [campuses(value) for value in narrative]
    if any(known or other for known, other in remaining):
        return any("石牌" in known for known, _ in remaining)
    return True


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
    # A bracketed announcement heading can name the talk even when the only
    # later line containing "讲座" is its time. Keep the extracted words.
    for heading in re.findall(r"【([^】\n]{2,400})】", text):
        if not re.search(r"通知|讲座|论坛|学术报告", heading):
            continue
        value = re.sub(r"^关于\s*", "", heading)
        value = re.sub(r"(?:讲座)?(?:报名成功|预约成功|报名|预约)?(?:的)?(?:通知|公告|预告)$", "", value)
        value = re.sub(r"^.*?分论坛(?:之)?", "", value)
        value = re.sub(r"^(?:“[^”]+”\s*)?(?:行业前沿|学术科研)(?:板块)?之", "", value)
        value = _clean(value)
        if value and value not in {"活动", "讲座", "报告", "学术报告", "报名", "预约"}:
            return value, heading
    # An unquoted heading is acceptable only when it is an announcement heading.
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^(?:[【\[（(]?讲座(?:通知|预告|提醒)?[】\]）)]?|学术报告(?:通知|预告)?)\s*[:：]", line):
            return _clean(line.split("：", 1)[-1].split(":", 1)[-1]), line
    return None, None


def notice_text(text: str, *, fallback_title: str = "", fallback_summary: str = "") -> tuple[str, str]:
    """Extract a display title and optional content excerpt, never a raw dump.

    This also formats older queued snapshots without replaying their messages
    or changing deduplication identities. Source text stays in the local store.
    """
    fields = _field_lines(text)
    title, _ = _title(fields)
    if not title and fallback_title and not _FIELD_BOUNDARY.search(fallback_title):
        title = fallback_title
    if not title:
        title = next((line.strip() for line in text.splitlines()
                      if _LECTURE.search(line) and not _FIELD_BOUNDARY.search(line)), "")
    title = " ".join(title.split()) or "讲座相关消息"
    if len(title) > 150:
        title = title[:149].rstrip() + "…"
    _, detail = lecture_summary(fields, title)
    # Explicit, caller-provided summaries remain useful when no raw message is
    # available. When raw text exists, only its content paragraphs are excerpts.
    if not text.strip() and not detail:
        detail = fallback_summary.strip()
    detail = " ".join((detail or "").split())
    prefix = f"讲座《{title}》："
    if detail.startswith(prefix):
        detail = detail[len(prefix):]
    if detail in (title, f"讲座《{title}》。", f"讲座《{title}》"):
        detail = ""
    if len(detail) > 160:
        detail = detail[:160].rstrip("，、；： ") + "…"
    return title, detail


def _signup_details(text: str, base: datetime | None) -> tuple[str | None, str, str | None]:
    """Copy opening time and a signup URL; a cutoff is never an opening time."""
    opening = ""
    for line in re.split(r"\n|[，。；;]", text):
        labelled = _SIGNUP_TIME.search(line)
        if not (labelled or _SIGNUP_OPEN.search(line)):
            continue
        value = line[labelled.end():] if labelled else line.strip()
        clock = _TIME.search(value)
        if _SIGNUP_CUTOFF.search(value[:clock.end()] if clock else value):
            continue
        # Keep the first endpoint of a signup window, including an unknown year.
        opening = (value[:clock.end()] if clock else value).strip()[:400]
        if opening:
            break
    opening_at = None
    if opening:
        days, date_errors = _dates(opening, base)
        clocks, time_errors = _times(opening)
        unique_days = _unique([day for day, _ in days])
        if len(unique_days) == len(clocks) == 1 and not (date_errors or time_errors):
            hour, minute = clocks[0][0]
            opening_at = _iso(datetime.combine(unique_days[0], datetime.min.time(), SHANGHAI)
                              .replace(hour=hour, minute=minute))
    # Prefer a labelled signup link. A single unlabelled URL is also useful in
    # an explicitly signup-related message. Never open a URL during extraction.
    urls = []
    for match in _SIGNUP_LINK.finditer(text):
        tail = text[match.end():].lstrip()
        line = tail.splitlines()[0] if tail else ""
        urls.extend(_URL.findall(line))
    if not urls and re.search(r"报名|预约|问卷", text):
        all_urls = _unique(_URL.findall(text))
        if len(all_urls) == 1:
            urls = all_urls
    signup_url = urls[0].rstrip(".,;!?)]}）】》") if urls else None
    return opening_at, opening, signup_url


def _candidate(message: dict[str, Any], text: str, *, lead: str | None = None, force_related=False) -> dict[str, Any] | None:
    analysis_text = _field_lines(text)
    raw_attachments = message.get("attachments", [])
    attachments = [item for item in raw_attachments if isinstance(item, dict)] if isinstance(raw_attachments, list) else []
    names = [str(item.get("name", "")).strip() for item in attachments if item.get("name")]
    # No title, announcement format, time, or confidence threshold is required.
    related = _LECTURE.search(text) or any(_LECTURE.search(name) for name in names)
    if not related and not force_related:
        return None
    if not campus_allowed(message, text):
        return None
    file_message = message.get("kind") == "file"
    if file_message:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        filename = (lines[1] if len(lines) > 1 and lines[0] == "文件" else
                    re.sub(r"^文件\s*", "", lines[0]) if lines else "")
        names = names or ([filename] if filename else [])
        lead = "文件消息；仅记录文件名，未下载、未读取文件正文"
    title, title_evidence = _title(analysis_text)
    title, detail = notice_text(text, fallback_title=title or next(
        (name for name in names if _LECTURE.search(name)), ""))
    if file_message:
        title, detail = next(iter(names), "文件消息"), ""
    summary = f"讲座《{title}》：{detail}" if detail else title
    evidence = {"title": title_evidence} if title_evidence else {}
    if names:
        evidence["attachments"] = "\n".join(names)
    if detail:
        evidence["summary"] = detail
    candidate: dict[str, Any] = {
        "title": title, "summary": summary, "analysis_policy": ANALYSIS_POLICY,
        "category": "lecture_notice", "status": "noticed",
        "start_at": None, "end_at": None, "activity_time_text": "",
        "signup_start_at": None, "signup_start_text": "", "signup_url": None,
        # These compatibility fields only describe calendar export, not delivery.
        "schedule_ready": False, "needs_review": False, "review_reasons": [],
        "evidence": evidence,
        "source_message_id": message.get("id"), "source_group": message.get("group"),
        "source_kind": message.get("kind", "text"),
        "source_sender": message.get("sender"), "source_sent_at": message.get("sent_at"),
        "source_text": text,
    }
    if lead or (names and not text.strip()):
        evidence["content_note"] = lead or "附件正文未读取"
        return candidate

    base, _ = _base_time(message.get("sent_at"))
    opening_at, opening, signup_url = _signup_details(analysis_text, base)
    candidate.update(signup_start_at=opening_at, signup_start_text=opening, signup_url=signup_url)
    if opening:
        evidence["signup_start"] = opening
    if signup_url:
        evidence["signup_url"] = signup_url

    event_lines = [line for line in analysis_text.splitlines() if _EVENT_TIME_LINE.search(line)
                   and not re.search(r"报名|预约|登记|截止|截至|签到|签退", line)]
    if not event_lines:
        event_lines = [line for line in analysis_text.splitlines()
                       if not re.search(r"报名|预约|登记|截止|截至|签到|签退|提前.{0,12}(?:到场|到达)", line)]
    schedule_text = "\n".join(event_lines)
    candidate["activity_time_text"] = time_excerpt(schedule_text)
    days, date_errors = _dates(schedule_text, base)
    clocks, time_errors = _times(schedule_text)
    unique_days = _unique([day for day, _ in days])
    unique_clocks = _unique([(start, end) for start, end, _ in clocks])
    if len(unique_days) == len(unique_clocks) == 1 and not (date_errors or time_errors):
        day = unique_days[0]
        start_clock, end_clock = unique_clocks[0]
        weekdays = _WEEKDAY.findall(schedule_text)
        if all(("日" if weekday == "天" else weekday) == "一二三四五六日"[day.weekday()]
               for weekday in weekdays):
            start = datetime.combine(day, datetime.min.time(), SHANGHAI).replace(hour=start_clock[0], minute=start_clock[1])
            candidate["start_at"] = _iso(start)
            evidence["start_at"] = f"{days[0][1]} {clocks[0][2]}"
            if end_clock:
                end = start.replace(hour=end_clock[0], minute=end_clock[1])
                if end > start:
                    candidate["end_at"] = _iso(end)
            candidate["schedule_ready"] = not end_clock or bool(candidate["end_at"])
    return candidate


def analyze_message(message: dict, *, candidate_factory=None) -> list[dict]:
    """Extract candidates from one message and its bounded forwarding tree.

    Children inherit only their group and a derived identity.  Forwarding time
    and forwarder identity are not evidence of the original sending time or
    sender.  Date calculations always use
    the message's sending date in UTC+08:00, never the machine's current date.
    Visible lecture clues in inaccessible forwards still produce a notification.
    """
    if not isinstance(message, dict):
        return []
    make_candidate = candidate_factory or _candidate
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
            item = make_candidate(merged, text, lead="转发结构存在循环，正文未继续解析")
            return [item] if item else []
        if depth > MAX_FORWARD_DEPTH or count >= MAX_MESSAGE_NODES:
            item = make_candidate(merged, text[:MAX_TEXT_LENGTH], lead="转发层数或条数超过解析上限，需人工展开核对")
            return [item] if item else []
        seen.add(id(node))
        count += 1
        if len(text) > MAX_TEXT_LENGTH:
            item = make_candidate(merged, text[:MAX_TEXT_LENGTH], lead="消息正文超过解析长度上限，需人工核对完整内容")
            return [item] if item else []
        children = merged.get("children", [])
        valid_children = [child for child in children if isinstance(child, dict)] if isinstance(children, list) else []
        found: list[dict] = []
        if merged.get("kind") == "forward":
            for child_index, child in enumerate(valid_children):
                if count >= MAX_MESSAGE_NODES:
                    item = make_candidate(merged, text, lead="转发条数超过解析上限，部分正文未解析")
                    if item:
                        found.append(item)
                    break
                child_metadata = {"group": merged.get("group"), "id": f"{merged.get('id') or 'message'}/child-{child_index + 1}"}
                found.extend(visit(child, child_metadata, depth + 1))
            if not valid_children:
                item = make_candidate(merged, text, lead="转发卡片没有可读取的子消息正文，需人工展开核对")
                if item:
                    found.append(item)
            return found
        item = make_candidate(merged, text)
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
