from datetime import datetime, timezone


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _stamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def export_ics(events: list[dict]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Local Wechat Lecture Tracker//ZH", "CALSCALE:GREGORIAN"]
    for event in events:
        ready = event.get("schedule_ready", not event.get("needs_review"))
        if (not ready and event.get("status") != "cancelled") or not event.get("start_at"):
            continue
        lines.extend(["BEGIN:VEVENT", f"UID:{event['id']}@wechat-tracker.local",
                      f"DTSTAMP:{_stamp(event['updated_at'])}", f"SEQUENCE:{event['version']}",
                      f"DTSTART:{_stamp(event['start_at'])}", f"SUMMARY:{_escape(event['title'])}"])
        if event.get("end_at"):
            lines.append(f"DTEND:{_stamp(event['end_at'])}")
        if event.get("status") == "cancelled":
            lines.append("STATUS:CANCELLED")
        description = "讲座摘要：" + (event.get("summary") or event["title"])
        description += "\n报名截止时间：" + (event.get("deadline_at") or event.get("deadline_text") or "未注明")
        lines.extend([f"DESCRIPTION:{_escape(description)}", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    # RFC 5545 folds physical lines at 75 UTF-8 octets without splitting characters.
    folded = []
    for line in lines:
        current = ""
        for char in line:
            if len((current + char).encode("utf-8")) > 75:
                folded.append(current)
                current = " "
            current += char
        folded.append(current)
    return "\r\n".join(folded) + "\r\n"
