"""Outbox delivery policy; transport and persistence are injected."""
from datetime import datetime, timedelta
import json

from ..domain.messages import UTC, parse_time, utc_text
from ..domain.policy import ANALYSIS_POLICY
from ..presentation.notifications import format_notification, format_notification_html
from .ports import MailSender, Repository


class NotificationDispatcher:
    def __init__(self, repository: Repository, config: dict, sender: MailSender, clock=None):
        self.repository, self.config, self.sender = repository, config, sender
        self.clock = clock if clock is not None else lambda: datetime.now(UTC)

    def dispatch(self, now: datetime | None = None, send=False) -> list[dict]:
        now = now or self.clock()
        repo = self.repository
        # Interrupted SMTP is at-least-once: an accepted message may be retried.
        with repo.transaction():
            repo.maintain_jobs(utc_text(now - timedelta(minutes=10)), bool(self.config.get("reminder_minutes", [])))
        jobs = repo.due_jobs(utc_text(now))
        latest_reminder = {(j["event_id"], j["version"]): j["id"] for j in jobs if j["reason"] == "reminder"}
        results = []
        for job in jobs:
            if job["reason"] == "reminder" and latest_reminder[(job["event_id"], job["version"])] != job["id"]:
                if send:
                    with repo.transaction():
                        repo.finish_job(job["id"], "coalesced")
                continue
            event = json.loads(job["payload"])
            if event.get("analysis_policy") != ANALYSIS_POLICY:
                continue
            if job["reason"] == "reminder" and event.get("start_at") and parse_time(event["start_at"]) <= now:
                if send:
                    with repo.transaction():
                        repo.finish_job(job["id"], "expired")
                continue
            subject, body = format_notification(event, job["reason"])
            item = dict(job_id=job["id"], reason=job["reason"], subject=subject, body=body, status="preview")
            if send:
                with repo.transaction():
                    claimed = repo.claim_job(job["id"], utc_text(now))
                if not claimed:
                    continue
                try:
                    html_body = format_notification_html(event, job["reason"])
                    if html_body is None:
                        self.sender(subject, body, self.config["email"])
                    else:
                        self.sender(subject, body, self.config["email"], html_body=html_body)
                except Exception as error:
                    attempts = job["attempts"] + 1
                    status = "dead" if attempts >= self.config.get("max_attempts", 5) else "pending"
                    due = utc_text(now + timedelta(seconds=min(3600, 60 * 2 ** (attempts - 1))))
                    with repo.transaction():
                        repo.fail_job(job["id"], status, attempts, type(error).__name__, due)
                    item.update(status=status, error=type(error).__name__)
                else:
                    with repo.transaction():
                        repo.finish_job(job["id"], "sent")
                    item["status"] = "sent"
            results.append(item)
        return results
