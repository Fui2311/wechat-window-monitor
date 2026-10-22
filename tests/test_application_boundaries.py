"""Architecture and adapter acceptance, beyond behavior-preserving unit tests."""
import ast
import copy
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from wechat_tracker.analyzer import analyze_message
from wechat_tracker.application.tracker import Tracker
from wechat_tracker.domain.messages import parse_time, utc_text
from wechat_tracker.engine import Engine
from wechat_tracker.persistence.repository import SQLiteRepository
from wechat_tracker.store import Store


class MemoryRepository:
    """Independent in-memory adapter for the notice ingestion contract."""
    def __init__(self):
        self.raw, self.notices, self.queued = {}, {}, []

    @contextmanager
    def transaction(self):
        previous = copy.deepcopy((self.raw, self.notices, self.queued))
        try:
            yield
        except Exception:
            self.raw, self.notices, self.queued = previous
            raise

    def message_exists(self, key):
        return key in self.raw

    def add_message(self, key, message, received_at):
        if key in self.raw:
            return False
        self.raw[key] = dict(payload=message, received_at=received_at)
        return True

    def event_exists(self, event_id):
        return event_id in self.notices

    def save_event(self, event, title, group):
        self.notices[event["id"]] = dict(event)

    def enqueue(self, event, reason, due_at, suffix):
        self.queued.append(dict(event=event, reason=reason, due_at=due_at))


class ApplicationBoundaryTests(unittest.TestCase):
    def test_application_imports_without_desktop_sql_http_or_smtp(self):
        script = """
import sys
import wechat_tracker.application.tracker
for name in ('sqlite3', 'smtplib', 'urllib.request', 'http.server', 'wechat_tracker.capture', 'wechat_tracker.config'):
    assert name not in sys.modules, name
"""
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_application_never_imports_an_adapter_or_embeds_sql(self):
        root = Path(__file__).resolve().parents[1] / "wechat_tracker"
        forbidden = {"sqlite3", "smtplib", "subprocess", "urllib", "http", "persistence", "store", "capture", "console", "config", "processing", "notify"}
        for path in (root / "application").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(set((node.module or "").split(".")) & forbidden, str(path))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(set(alias.name.split(".")) & forbidden, str(path))

    def test_ingestion_runs_with_an_independent_repository_and_rolls_back_as_a_unit(self):
        repo = MemoryRepository()
        now = parse_time("2026-10-22T12:00:00+08:00")
        message = dict(id="synthetic-1", group="测试群", text="学术报告：测试讲座")
        def processor(message, config, *, report):
            return analyze_message(message)
        tracker = Tracker(repo, dict(group="测试群"), processor=processor, rules_analyzer=analyze_message,
                          sender=lambda *a, **kw: self.fail("preview must not send"), clock=lambda: now)
        self.assertEqual(len(tracker.ingest(message)["events"]), 1)
        self.assertTrue(tracker.ingest(message)["duplicate"])
        self.assertEqual(len(repo.queued), 1)
        self.assertEqual(repo.queued[0]["due_at"], utc_text(now))
        tracker.processor = lambda *args, **kw: [dict(category="lecture_notice")]  # Missing required title.
        with self.assertRaises(KeyError):
            tracker.ingest(dict(message, id="synthetic-2", text="另一场讲座"))
        self.assertEqual(len(repo.raw), 1)
        self.assertEqual(len(repo.notices), 1)
        self.assertEqual(len(repo.queued), 1)

    def test_sqlite_job_claim_is_exclusive_between_connections_and_preview_does_not_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.sqlite3"
            first, second = Store(path), Store(path)
            try:
                now = parse_time("2026-10-22T12:00:00+08:00")
                engine = Engine(first, dict(group="测试群", email={}), clock=lambda: now)
                engine.ingest(dict(id="m1", group="测试群", text="学术报告"))
                self.assertEqual(engine.dispatch()[0]["status"], "preview")
                self.assertEqual(first.jobs()[0]["status"], "pending")
                one, two = SQLiteRepository(first), SQLiteRepository(second)
                job_id = one.due_jobs(utc_text(now))[0]["id"]
                with one.transaction():
                    self.assertTrue(one.claim_job(job_id, utc_text(now)))
                with two.transaction():
                    self.assertFalse(two.claim_job(job_id, utc_text(now)))
            finally:
                first.close()
                second.close()
