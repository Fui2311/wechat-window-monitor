from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata

from .analyzer import analyze_message
from .notify import format_notification, send_email
from .store import Store

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


class Engine:
    def __init__(self, store: Store, config: dict):
        self.store, self.config = store, config

    def ingest(self, message: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        validate_message(message, self.config["group"])
        message = dict(message, group=self.config["group"])
        # Observation timestamps are transport metadata, never message identity.
        stable = {k: v for k, v in message.items() if k not in ("observed_at", "source_meta")}
        key = digest(stable)
        candidates = analyze_message(message)
        result = {"duplicate": False, "events": []}
        with self.store.db:
            inserted = self.store.db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?)",
                (key, json.dumps(message, ensure_ascii=False), utc_text(now)),
            ).rowcount
            if not inserted:
                return {"duplicate": True, "events": []}
            for candidate in candidates:
                event = self._upsert(candidate, now)
                if event:
                    result["events"].append(event)
        return result

    def _upsert(self, candidate: dict, now: datetime) -> dict | None:
        candidate = dict(candidate)
        candidate.setdefault("needs_review", False)
        candidate.setdefault("review_reasons", [])
        candidate.setdefault("status", "active")
        if not candidate.get("title"):
            candidate.update(title="待核实讲座线索", needs_review=True)
        # Absolute event times are validated independently of extractor confidence.
        for field in ("start_at", "end_at", "arrival_at", "deadline_at"):
            if candidate.get(field):
                try:
                    parse_time(candidate[field])
                except (ValueError, TypeError):
                    candidate[field] = None
                    candidate["needs_review"] = True
                    candidate["schedule_ready"] = False
                    candidate["review_reasons"].append(f"{field}时间无效")
        if not candidate.get("start_at") and candidate["status"] != "cancelled":
            candidate["needs_review"] = True
            candidate["schedule_ready"] = False
        group = candidate["source_group"] = self.config["group"]
        normalized = title_key(candidate["title"])
        matches = [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT payload FROM events WHERE title_key=? AND group_name=?",
            (normalized, group))]
        matches = [e for e in matches if schedule_ready(e) or e.get("record_type") == "event"]
        is_update = candidate.get("category") == "lecture_update" or candidate["status"] == "cancelled"
        start = candidate.get("start_at")
        same_day = [e for e in matches if e.get("start_at") and start and
                    parse_time(e["start_at"]).astimezone(SHANGHAI).date() ==
                    parse_time(start).astimezone(SHANGHAI).date()]
        exact = [e for e in same_day if parse_time(e["start_at"]) == parse_time(start)]
        old = exact[0] if len(exact) == 1 else (same_day[0] if is_update and len(same_day) == 1 else None)
        # Explicit amendments can reschedule one unambiguous event; vague ones require review.
        if is_update and not old and len(matches) == 1:
            old = matches[0]
        elif is_update and not old:
            candidate["needs_review"] = True
            candidate["schedule_ready"] = False
            candidate["review_reasons"].append("未找到唯一可关联的原讲座，请核实改期/取消对象")
        if old and old.get("status") == "cancelled" and candidate["status"] != "cancelled":
            candidate["needs_review"] = True
            candidate["schedule_ready"] = False
            candidate["review_reasons"].append("原讲座已取消；新通知需人工核实是否恢复举办")
            old = None
        if old and not schedule_ready(candidate):
            # Incomplete amendments become independent review items; never overwrite a known event.
            old = None
        if old:
            old_source, new_source = old.get("source_sent_at"), candidate.get("source_sent_at")
            if old_source and new_source and parse_time(new_source) < parse_time(old_source):
                return None  # Late replay must not revert a newer revision.
            merged = dict(old)
            for field, value in candidate.items():
                if value not in (None, "", [], {}):
                    merged[field] = value
            if start and old.get("start_at") and parse_time(start) != parse_time(old["start_at"]):
                # A new start does not authorize retaining an end/arrival on the old date.
                merged["end_at"] = candidate.get("end_at")
                merged["arrival_at"] = candidate.get("arrival_at")
                if not merged["arrival_at"] and old.get("arrival_at"):
                    lead = parse_time(old["start_at"]) - parse_time(old["arrival_at"])
                    merged["arrival_at"] = (parse_time(start) - lead).isoformat(timespec="seconds")
            comparable = ("title", "start_at", "end_at", "arrival_at", "location", "speakers",
                          "deadline_at", "requirements", "needs_review", "schedule_ready", "status")
            if all(merged.get(k) == old.get(k) for k in comparable):
                return None
            event = merged
            event_id, version = old["id"], old["version"] + 1
            reason = "cancelled" if event["status"] == "cancelled" else "update"
        else:
            event = candidate
            event["record_type"] = "event" if schedule_ready(event) and event["status"] != "cancelled" else "review"
            # A review lead is keyed to its evidence. Ordinary repeated announcements merge by date.
            identity = (group, normalized, start if schedule_ready(candidate) else
                        digest([candidate.get("source_message_id"), candidate.get("source_text")]))
            event_id = digest(identity)[:24]
            existing = self.store.db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
            if existing:
                return None
            version = 1
            reason = "review" if event["needs_review"] else "new"
        event.update(id=event_id, version=version, updated_at=utc_text(now))
        self.store.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?)",
                              (event_id, normalized, group, json.dumps(event, ensure_ascii=False)))
        if old:
            self.store.db.execute("UPDATE outbox SET status='superseded' WHERE event_id=? AND status IN ('pending','sending')",
                                  (event_id,))
        self._queue(event, reason, now)
        if schedule_ready(event) and event["status"] != "cancelled" and event.get("start_at"):
            # Reminders refer to arrival when stated, otherwise to the lecture start.
            anchor = parse_time(event.get("arrival_at") or event["start_at"])
            for minutes in sorted(set(self.config.get("reminder_minutes", [1440, 60])), reverse=True):
                due = anchor - timedelta(minutes=minutes)
                if due > now:  # Avoid a burst of catch-up reminders for old announcements.
                    self._queue(event, "reminder", due, str(minutes))
        return event

    def _queue(self, event: dict, reason: str, due: datetime, suffix=""):
        self.store.db.execute("""INSERT OR IGNORE INTO outbox
            (dedup_key,event_id,version,reason,due_at,payload) VALUES (?,?,?,?,?,?)""",
            (f"{event['id']}:{event['version']}:{reason}:{suffix}", event["id"], event["version"],
             reason, utc_text(due), json.dumps(event, ensure_ascii=False)))

    def dispatch(self, now: datetime | None = None, send=False) -> list[dict]:
        now = now or datetime.now(UTC)
        # A worker interrupted during SMTP may have sent already: recovery is at-least-once.
        with self.store.db:
            self.store.db.execute("""UPDATE outbox SET status='pending',claimed_at=NULL
                WHERE status='sending' AND claimed_at < ?""", (utc_text(now - timedelta(minutes=10)),))
            self.store.db.execute("""UPDATE outbox SET status='superseded',claimed_at=NULL
                WHERE status IN ('pending','sending') AND EXISTS (
                    SELECT 1 FROM events WHERE events.id=outbox.event_id
                    AND (CAST(json_extract(events.payload,'$.version') AS INTEGER) != outbox.version
                    OR (json_extract(events.payload,'$.status')='cancelled' AND outbox.reason IN ('new','update','reminder')))
                )""")
        jobs = self.store.db.execute(
            "SELECT * FROM outbox WHERE status='pending' AND due_at<=? ORDER BY due_at,id LIMIT 100",
            (utc_text(now),)).fetchall()
        latest_reminder = {}
        for job in jobs:
            if job["reason"] == "reminder":
                latest_reminder[(job["event_id"], job["version"])] = job["id"]
        results = []
        for job in jobs:
            if job["reason"] == "reminder" and latest_reminder[(job["event_id"], job["version"])] != job["id"]:
                if send:
                    with self.store.db:
                        self.store.db.execute("UPDATE outbox SET status='coalesced' WHERE id=? AND status='pending'", (job["id"],))
                continue
            event = json.loads(job["payload"])
            # A suspended computer must not deliver a 'soon' reminder after arrival/start.
            anchor = event.get("arrival_at") or event.get("start_at")
            if job["reason"] == "reminder" and anchor and parse_time(anchor) <= now:
                if send:
                    with self.store.db:
                        self.store.db.execute("UPDATE outbox SET status='expired' WHERE id=? AND status='pending'", (job["id"],))
                continue
            subject, body = format_notification(event, job["reason"])
            item = {"job_id": job["id"], "reason": job["reason"], "subject": subject, "body": body,
                    "status": "preview"}
            if send:
                with self.store.db:
                    claimed = self.store.db.execute("""UPDATE outbox SET status='sending',claimed_at=?
                        WHERE id=? AND status='pending' AND EXISTS (
                        SELECT 1 FROM events WHERE events.id=outbox.event_id
                        AND CAST(json_extract(events.payload,'$.version') AS INTEGER)=outbox.version
                        )""", (utc_text(now), job["id"])).rowcount
                if not claimed:
                    continue
                try:
                    send_email(subject, body, self.config["email"])
                except Exception as error:
                    attempts = job["attempts"] + 1
                    status = "dead" if attempts >= self.config.get("max_attempts", 5) else "pending"
                    with self.store.db:
                        self.store.db.execute("""UPDATE outbox SET status=?,attempts=?,last_error=?,
                            due_at=?,claimed_at=NULL WHERE id=? AND status='sending'""", (status, attempts, type(error).__name__,
                            utc_text(now + timedelta(seconds=min(3600, 60 * 2 ** (attempts - 1)))), job["id"]))
                    item.update(status=status, error=type(error).__name__)
                else:
                    with self.store.db:
                        self.store.db.execute("UPDATE outbox SET status='sent',claimed_at=NULL WHERE id=? AND status='sending'", (job["id"],))
                    item["status"] = "sent"
            results.append(item)
        return results

    def capture_snapshot(self, messages: list[dict], include_existing=False, now=None) -> list[dict]:
        now = now or datetime.now(UTC)
        group = self.config["group"]
        first = not self.store.db.execute("SELECT 1 FROM capture_state WHERE group_name=?", (group,)).fetchone()
        results = []
        for message in messages:
            validate_message(message, group)
            stable = {k: v for k, v in message.items() if k not in ("observed_at", "source_meta")}
            fingerprint = digest(stable)
            if self.store.db.execute("SELECT 1 FROM capture_seen WHERE group_name=? AND fingerprint=?", (group, fingerprint)).fetchone():
                continue
            if not first or include_existing:
                results.append(self.ingest(message, now))
            with self.store.db:
                self.store.db.execute("INSERT OR IGNORE INTO capture_seen VALUES (?,?)", (group, fingerprint))
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO capture_state VALUES (?,?)", (group, utc_text(now)))
        return results

