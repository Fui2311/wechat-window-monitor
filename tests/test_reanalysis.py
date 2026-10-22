from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from wechat_tracker.analyzer import ANALYSIS_POLICY
from wechat_tracker.engine import Engine, parse_time
from wechat_tracker.store import Store


class ReanalysisTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.now = parse_time("2026-10-15T13:00:00+08:00")
        self.engine = Engine(self.store, {"group": "测试群", "reminder_minutes": [1440, 60], "email": {}})
        message = {"group": "测试群", "id": "1", "text": "讲座主题：《研究方法》\n讲座时间：2026年10月17日14:30\n报名截止：2026年10月16日18:00"}
        self.engine.capture_snapshot([message], include_existing=True, now=self.now)
        # Represent an old installation with stale arrival-based queue snapshots.
        with self.store.db:
            for row in self.store.db.execute("SELECT id,payload FROM events").fetchall():
                value = json.loads(row["payload"])
                value.pop("analysis_policy")
                value["arrival_at"] = "2026-10-17T14:15:00+08:00"
                self.store.db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(value), row["id"]))
            for row in self.store.db.execute("SELECT id,payload,due_at,reason FROM outbox").fetchall():
                value = json.loads(row["payload"])
                value.pop("analysis_policy")
                due = parse_time(row["due_at"])
                if row["reason"] == "reminder":
                    due -= timedelta(minutes=15)
                self.store.db.execute("UPDATE outbox SET payload=?,due_at=? WHERE id=?",
                                      (json.dumps(value), due.isoformat(), row["id"]))

    def test_rebuild_preserves_sources_dedup_audit_and_corrects_times(self):
        result = self.engine.rebuild_analysis(self.now)
        self.assertTrue(result["changed"])
        self.assertEqual(result["events"], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0], 1)
        jobs = self.store.jobs()
        self.assertEqual(sum(job["status"] == "superseded" for job in jobs), 1)
        reminders = [job for job in jobs if job["status"] == "pending" and job["reason"] == "reminder"]
        self.assertEqual(reminders, [])
        self.assertEqual(self.store.events()[0]["version"], 2)
        self.assertEqual(self.store.events()[0]["analysis_policy"], ANALYSIS_POLICY)
        self.assertNotIn("arrival_at", self.store.events()[0])
        self.assertFalse(self.engine.rebuild_analysis(self.now)["changed"])

    def test_old_false_cancellation_is_rebuilt_as_a_notice_without_duplicate_delivery(self):
        with self.store.db:
            for row in self.store.db.execute("SELECT id,payload FROM events").fetchall():
                event = json.loads(row["payload"])
                event.update(status="cancelled", category="lecture_update", needs_review=True, confidence=0.15)
                self.store.db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(event), row["id"]))
        self.engine.rebuild_analysis(self.now)
        actual, = self.store.events()
        self.assertEqual(actual["record_type"], "notice")
        self.assertEqual(actual["status"], "noticed")
        self.assertNotIn("confidence", actual)
        self.assertEqual(len(self.engine.dispatch(self.now)), 1)
        self.assertFalse(self.engine.rebuild_analysis(self.now)["changed"])

    def test_sent_or_inflight_data_is_never_replayed(self):
        for status in ("sent", "sending"):
            with self.store.db:
                self.store.db.execute("UPDATE outbox SET status=?", (status,))
            before = (self.store.events(), self.store.jobs())
            with self.assertRaisesRegex(ValueError, "不能自动重放"):
                self.engine.rebuild_analysis(self.now)
            self.assertEqual((self.store.events(), self.store.jobs()), before)

    def test_rebuild_failure_rolls_back_all_derived_data(self):
        before = (self.store.events(), self.store.jobs())
        with patch("wechat_tracker.engine.analyze_message", side_effect=ValueError("invalid source")):
            with self.assertRaises(ValueError):
                self.engine.rebuild_analysis(self.now)
        self.assertEqual((self.store.events(), self.store.jobs()), before)

    def test_old_snapshots_are_never_dispatched_without_rebuild(self):
        with patch("wechat_tracker.engine.send_email") as smtp:
            self.assertEqual(self.engine.dispatch(self.now, send=True), [])
        smtp.assert_not_called()

    def test_rebuild_does_not_schedule_missed_reminders(self):
        self.engine.rebuild_analysis(parse_time("2026-10-17T14:20:00+08:00"))
        self.assertFalse(any(job["status"] == "pending" and job["reason"] == "reminder" for job in self.store.jobs()))


if __name__ == "__main__":
    unittest.main()
