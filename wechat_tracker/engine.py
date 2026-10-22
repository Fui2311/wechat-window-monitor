"""Backward-compatible composition root for the tracking use cases.

Default adapters are wired here. Applications can inject a Repository, processor,
rule analyzer or sender without importing desktop capture or an HTTP server.
"""
from datetime import datetime

from .analyzer import analyze_message
from .application.tracker import Tracker
from .domain.messages import (UTC, SHANGHAI, digest, parse_time, schedule_ready,
                              stable_message, title_key, utc_text, validate_message)
from .notify import send_email
from .persistence.repository import SQLiteRepository
from .processing import process_message
from .store import Store


class Engine(Tracker):
    def __init__(self, store: Store, config: dict, *, repository=None,
                 processor=None, rules_analyzer=None, sender=None, clock=None):
        # Late binding also preserves existing integrations that replace these
        # adapters at runtime. The use cases themselves never patch globals.
        rules = rules_analyzer if rules_analyzer is not None else lambda message: analyze_message(message)
        process = processor if processor is not None else lambda message, config, *, report: process_message(
            message, config, rules_analyzer=rules, report=report)
        mail = sender if sender is not None else lambda *args, **kwargs: send_email(*args, **kwargs)
        super().__init__(repository if repository is not None else SQLiteRepository(store),
                         config, processor=process, rules_analyzer=rules, sender=mail,
                         clock=clock if clock is not None else lambda: datetime.now(UTC))
        self.store = store
