"""Event identity, revision and notification scheduling policies."""
from datetime import datetime, timedelta

from ..domain.messages import SHANGHAI, digest, parse_time, schedule_ready, title_key, utc_text
from ..domain.policy import ANALYSIS_POLICY
from ..presentation.notifications import notification_format
from .ports import Repository


class EventRegistry:
    def __init__(self, repository: Repository, config: dict):
        self.repository = repository
        self.config = config
        self.version_floors = {}

    def upsert(self, candidate: dict, now: datetime) -> dict | None:
        if candidate.get("category") == "lecture_notice":
            return self._store_notice(candidate, now)
        candidate = dict(candidate)
        candidate["analysis_policy"] = ANALYSIS_POLICY
        for field in ("arrival_at", "location", "speakers", "requirements"):
            candidate.pop(field, None)
        candidate.setdefault("needs_review", False)
        candidate.setdefault("review_reasons", [])
        candidate.setdefault("status", "active")
        if not candidate.get("title"):
            candidate.update(title="待核实讲座线索", needs_review=True)
        # Absolute event times are validated independently of extractor confidence.
        for field in ("start_at", "end_at", "deadline_at"):
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
        matches = self.repository.matching_events(normalized, group)
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
            if not candidate.get("evidence", {}).get("summary") and old.get("summary"):
                merged["summary"] = old["summary"]
            if candidate.get("deadline_text"):
                merged["deadline_at"] = candidate.get("deadline_at")
            if start and old.get("start_at") and parse_time(start) != parse_time(old["start_at"]):
                # A new start does not authorize an end on the old date.
                merged["end_at"] = candidate.get("end_at")
            comparable = ("title", "summary", "start_at", "end_at", "deadline_at",
                          "needs_review", "schedule_ready", "status")
            # A different unresolved cutoff is a meaningful update; equivalent
            # spellings of a resolved absolute cutoff do not create extra mail.
            if not merged.get("deadline_at") and merged.get("deadline_text") != old.get("deadline_text"):
                comparable += ("deadline_text",)
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
            existing = self.repository.event_exists(event_id)
            if existing:
                return None
            version = self.version_floors.get(event_id, 0) + 1
            reason = "review" if event["needs_review"] else "new"
        event.update(id=event_id, version=version, updated_at=utc_text(now))
        self.repository.save_event(event, normalized, group)
        if old:
            self.repository.supersede_event_jobs(event_id)
        self._queue(event, reason, now)
        if schedule_ready(event) and event["status"] != "cancelled" and event.get("start_at"):
            anchor = parse_time(event["start_at"])
            for minutes in sorted(set(self.config.get("reminder_minutes", [])), reverse=True):
                due = anchor - timedelta(minutes=minutes)
                if due > now:  # Avoid a burst of catch-up reminders for old announcements.
                    self._queue(event, "reminder", due, str(minutes))
        return event

    def _store_notice(self, candidate: dict, now: datetime) -> dict | None:
        """Notify once per source message, without merging or judging events."""
        notice = dict(candidate, analysis_policy=ANALYSIS_POLICY,
                      source_group=self.config["group"], record_type="notice")
        notice["mail_format"] = notification_format(notice)
        identity = (self.config["group"], notice.get("source_message_id"), notice.get("source_text"))
        notice_id = digest(identity)[:24]
        if self.repository.event_exists(notice_id):
            return None
        notice.update(id=notice_id, version=self.version_floors.get(notice_id, 0) + 1,
                      updated_at=utc_text(now))
        self.repository.save_event(notice, title_key(notice["title"]), self.config["group"])
        self._queue(notice, "new", now)
        return notice

    def _queue(self, event: dict, reason: str, due: datetime, suffix=""):
        self.repository.enqueue(event, reason, utc_text(due), suffix)
