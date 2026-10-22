"""Capture/ingestion and offline rebuild use cases, with explicit adapters."""
from datetime import datetime

from ..domain.messages import UTC, digest, parse_time, stable_message, utc_text, validate_message
from ..domain.policy import ANALYSIS_POLICY
from .delivery import NotificationDispatcher
from .events import EventRegistry
from .ports import MailSender, MessageProcessor, Repository, RulesAnalyzer


class Tracker:
    def __init__(self, repository: Repository, config: dict, *, processor: MessageProcessor,
                 rules_analyzer: RulesAnalyzer, sender: MailSender, clock=None):
        self.clock = clock if clock is not None else lambda: datetime.now(UTC)
        self.repository = repository
        self.config = config
        self.processor = processor
        self.rules_analyzer = rules_analyzer
        self.registry = EventRegistry(repository, config)
        self.dispatcher = NotificationDispatcher(repository, config, sender, self.clock)
        self.last_processing_report = {}

    def ingest(self, message: dict, now: datetime | None = None) -> dict:
        now = now or self.clock()
        validate_message(message, self.config["group"])
        message = dict(message, group=self.config["group"])
        key = digest(stable_message(message))
        if self.repository.message_exists(key):
            return {"duplicate": True, "events": []}
        candidates = self.processor(message, self.config, report=self.last_processing_report)
        result = {"duplicate": False, "events": []}
        with self.repository.transaction():
            if not self.repository.add_message(key, message, utc_text(now)):
                return {"duplicate": True, "events": []}
            for candidate in candidates:
                event = self.registry.upsert(candidate, now)
                if event:
                    result["events"].append(event)
        return result

    def capture_snapshot(self, messages: list[dict], include_existing=False, now=None) -> list[dict]:
        now = now or self.clock()
        group = self.config["group"]
        first = not self.repository.capture_initialized(group)
        results = []
        for message in messages:
            validate_message(message, group)
            fingerprint = digest(stable_message(message))
            if self.repository.capture_seen(group, fingerprint):
                continue
            if not first or include_existing:
                results.append(self.ingest(message, now))
            with self.repository.transaction():
                self.repository.mark_seen(group, fingerprint)
        with self.repository.transaction():
            self.repository.mark_capture(group, utc_text(now))
        return results

    def dispatch(self, now: datetime | None = None, send=False) -> list[dict]:
        return self.dispatcher.dispatch(now, send)

    def rebuild_analysis(self, now: datetime | None = None) -> dict:
        """Caller stops the service and verifies a backup before this offline operation."""
        now = now or self.clock()
        repo = self.repository
        if repo.has_delivered_jobs():
            raise ValueError("已有已发送或发送中记录，不能自动重放；需单独审核迁移")
        old_events = repo.events()
        if all(e.get("analysis_policy") == ANALYSIS_POLICY for e in old_events) and all(
                j.get("analysis_policy") == ANALYSIS_POLICY for j in repo.pending_payloads()):
            return {"changed": False, "events": len(old_events)}
        messages = repo.messages()
        if old_events and not messages:
            raise ValueError("缺少原始消息，不能安全重算旧活动")
        floors = repo.version_floors()
        for event in old_events:
            floors[event["id"]] = max(event["version"], floors.get(event["id"], 0))
        self.registry.version_floors = floors
        try:
            with repo.transaction():
                repo.begin_rebuild()
                for row in messages:
                    message = row["payload"]
                    validate_message(message, self.config["group"])
                    for candidate in self.rules_analyzer(message):
                        self.registry.upsert(candidate, parse_time(row["received_at"]))
                repo.expire_reminders(utc_text(now))
        finally:
            self.registry.version_floors = {}
        return {"changed": True, "messages": len(messages), "events": len(repo.events()),
                "pending": repo.pending_count(), "analysis_policy": ANALYSIS_POLICY}
