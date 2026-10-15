from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.engine import Engine, parse_time
from wechat_tracker.store import Store
from wechat_tracker.calendar import export_ics

GROUP = "2026级计算机研究生通知群"
NOW = parse_time("2026-10-15T13:00:00+08:00")


def event(**overrides):
    value = dict(title="科研创新是如何炼成的", category="lecture", start_at="2026-10-17T14:30:00+08:00",
                 end_at="2026-10-17T16:30:00+08:00", arrival_at="2026-10-17T14:15:00+08:00",
                 location="东阶梯", speakers=["葛仕明", "丁圣勇"], requirements=[],
                 needs_review=False, review_reasons=[], source_group=GROUP, source_sender="教务",
                 source_text="原始证据", source_message_id="1", source_sent_at="2026-10-15T12:55:00+08:00",
                 status="active", confidence=0.95, evidence={})
    value.update(overrides)
    return value


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.config = dict(group=GROUP, reminder_minutes=[1440, 60], max_attempts=2, email={})
        self.engine = Engine(self.store, self.config)

    def tearDown(self):
        self.store.close()

    def ingest(self, candidate=None, identifier="1"):
        with patch("wechat_tracker.engine.analyze_message", return_value=[candidate or event()]):
            return self.engine.ingest(dict(id=identifier, group=GROUP, text="讲座"), NOW)

    def test_screenshot_end_to_end(self):
        path = Path(__file__).resolve().parent.parent / "examples" / "screenshot.jsonl"
        message = json.loads(path.read_text(encoding="utf-8"))
        result = self.engine.ingest(message, NOW)
        actual = result["events"][0]
        self.assertEqual(actual["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertNotIn("arrival_at", actual)
        self.assertFalse(actual["needs_review"])
        self.assertTrue(actual["schedule_ready"])
        self.assertEqual(len(self.store.jobs()), 3)

    def test_duplicate_and_preview_do_not_consume(self):
        self.ingest()
        self.assertTrue(self.ingest()["duplicate"])
        self.assertEqual(len(self.engine.dispatch(NOW)), 1)
        self.assertEqual(len(self.engine.dispatch(NOW)), 1)
        self.assertEqual(len(self.store.jobs()), 3)

    def test_reminder_uses_start_even_with_legacy_arrival_and_does_not_replay_missed(self):
        self.ingest()
        reminders = [j for j in self.store.jobs() if j["reason"] == "reminder"]
        self.assertEqual([j["due_at"] for j in reminders], ["2026-10-16T06:30:00+00:00", "2026-10-17T05:30:00+00:00"])
        with patch("wechat_tracker.engine.send_email") as send:
            self.engine.dispatch(parse_time("2026-10-18T00:00:00+08:00"), True)
        self.assertEqual(send.call_count, 1)  # new-event digest only, no stale imminent alarms

    def test_update_cancels_old_pending_jobs(self):
        original = self.ingest()["events"][0]
        amended = event(start_at="2026-10-18T14:30:00+08:00", end_at="2026-10-18T16:30:00+08:00",
                        arrival_at="2026-10-18T14:15:00+08:00", category="lecture_update",
                        source_sent_at="2026-10-15T13:00:00+08:00")
        new = self.ingest(amended, "2")["events"][0]
        self.assertEqual(new["id"], original["id"])
        self.assertEqual(new["version"], 2)
        self.assertEqual(sum(j["status"] == "superseded" for j in self.store.jobs()), 3)

    def test_ambiguous_amendment_does_not_overwrite(self):
        original = self.ingest()["events"][0]
        self.ingest(event(category="lecture_update", needs_review=True, start_at=None,
                          source_text="改到明天（无可靠消息日期）"), "2")
        self.assertEqual(self.store.events()[0]["start_at"], original["start_at"])
        self.assertEqual(len(self.store.events()), 2)

    def test_cancel_stops_reminders(self):
        self.ingest()
        self.ingest(event(category="lecture_update", status="cancelled", start_at=None, end_at=None,
                          arrival_at=None), "2")
        pending = [j for j in self.store.jobs() if j["status"] == "pending"]
        self.assertEqual([j["reason"] for j in pending], ["cancelled"])
        calendar = export_ics(self.store.events())
        self.assertIn("STATUS:CANCELLED", calendar)
        self.assertIn("SEQUENCE:2", calendar)

    def test_cancel_supersedes_crashed_worker_and_no_old_reminder_recovers(self):
        self.ingest()
        with self.store.db:
            self.store.db.execute("UPDATE outbox SET status='sending',claimed_at='2026-10-15T00:00:00+00:00' WHERE reason='reminder'")
        self.ingest(event(category="lecture_update", status="cancelled", start_at=None, end_at=None,
                          arrival_at=None), "2")
        self.assertEqual([j["reason"] for j in self.engine.dispatch(parse_time("2026-10-17T12:00:00+08:00"))], ["cancelled"])

    def test_catchup_reminders_are_coalesced(self):
        self.ingest()
        preview = self.engine.dispatch(parse_time("2026-10-17T13:45:00+08:00"))
        self.assertEqual(sum(j["reason"] == "reminder" for j in preview), 1)

    def test_untimed_reschedule_does_not_inherit_old_end(self):
        self.ingest()
        actual = self.ingest(event(category="lecture_update", start_at="2026-10-18T14:30:00+08:00",
                            end_at=None, arrival_at=None), "2")["events"][0]
        self.assertIsNone(actual["end_at"])
        self.assertNotIn("arrival_at", actual)

    def test_same_title_different_time_can_be_two_sessions(self):
        self.ingest()
        self.ingest(event(start_at="2026-10-17T19:30:00+08:00", arrival_at=None), "2")
        self.assertEqual(len(self.store.events()), 2)

    def test_attached_forward_children_change_capture_fingerprint(self):
        parent = dict(id="f", group=GROUP, kind="forward", text="聊天记录")
        self.engine.capture_snapshot([parent], now=NOW)
        parent["children"] = [dict(id="c", text="新正文")]
        with patch("wechat_tracker.engine.analyze_message", return_value=[]):
            results = self.engine.capture_snapshot([parent], now=NOW)
        self.assertEqual(len(results), 1)

    def test_ambiguous_cancellation_review_is_not_silently_dropped(self):
        self.ingest(event(category="lecture_update", status="cancelled", start_at=None))
        preview = self.engine.dispatch(NOW)
        self.assertEqual([j["reason"] for j in preview], ["review"])

    def test_ambiguous_cancellation_does_not_cancel_known_event(self):
        self.ingest()
        self.ingest(event(category="lecture_update", status="cancelled", schedule_ready=False,
                          needs_review=True, source_text="取消日期对象存在歧义"), "2")
        self.assertEqual(self.store.events()[0]["status"], "active")
        self.assertEqual(self.store.events()[1]["record_type"], "review")

    def test_review_lead_does_not_pollute_confirmed_update_matching(self):
        original = self.ingest()["events"][0]
        self.ingest(event(needs_review=True, schedule_ready=False, category="lecture_update", source_text="不完整修改"), "2")
        changed = self.ingest(event(deadline_at="2026-10-16T18:00:00+08:00", category="lecture_update"), "3")["events"][0]
        self.assertEqual(changed["id"], original["id"])
        self.assertEqual(changed["version"], 2)
        self.assertEqual(changed["deadline_at"], "2026-10-16T18:00:00+08:00")

    def test_review_never_schedules(self):
        self.ingest(event(needs_review=True))
        self.assertEqual([j["reason"] for j in self.store.jobs()], ["review"])
        self.assertNotIn("BEGIN:VEVENT", export_ics(self.store.events()))

    def test_smtp_retry_and_dead_letter(self):
        self.ingest()
        with patch("wechat_tracker.engine.send_email", side_effect=OSError("secret-password")):
            first = self.engine.dispatch(NOW, True)
            self.assertEqual(first[0]["status"], "pending")
            self.assertEqual(self.engine.dispatch(NOW, True), [])
            second = self.engine.dispatch(parse_time("2026-10-15T13:01:01+08:00"), True)
        self.assertEqual(second[0]["status"], "dead")
        self.assertNotIn("secret-password", json.dumps(self.store.jobs()))

    def test_capture_first_snapshot_is_baseline(self):
        message = dict(id="1", group=GROUP, text="无关消息", observed_at=NOW.isoformat())
        self.assertEqual(self.engine.capture_snapshot([message], now=NOW), [])
        message["observed_at"] = "2026-10-15T14:00:00+08:00"
        self.assertEqual(self.engine.capture_snapshot([message], now=NOW), [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_whitelist_and_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.ingest(dict(group="其他群", text="讲座"), NOW)
        with self.assertRaises(ValueError):
            self.engine.ingest(dict(group=GROUP, sent_at="2026-10-15T13:00:00", text="讲座"), NOW)

    def test_ics_utf8_folding(self):
        self.ingest(event(title="中文" * 100))
        data = export_ics(self.store.events())
        self.assertIn("DTSTART:20261017T063000Z", data)
        self.assertTrue(all(len(line.encode()) <= 75 for line in data.split("\r\n")))

    def test_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.sqlite3"
            store = Store(path)
            engine = Engine(store, self.config)
            with patch("wechat_tracker.engine.analyze_message", return_value=[event()]):
                engine.ingest(dict(group=GROUP, text="讲座"), NOW)
            store.close()
            reopened = Store(path)
            try:
                self.assertEqual(len(reopened.events()), 1)
                self.assertEqual(len(reopened.jobs()), 3)
            finally:
                reopened.close()

    def test_irrelevant_changes_never_generate_update_mail(self):
        self.ingest()
        self.assertEqual(self.ingest(event(location="新地点", speakers=["新人"],
                                          arrival_at="2026-10-17T14:00:00+08:00", requirements=["签到"]), "2")["events"], [])
        self.assertEqual(len(self.store.jobs()), 3)

    def test_reminder_between_old_arrival_and_start_is_still_valid(self):
        self.ingest()
        with patch("wechat_tracker.engine.send_email") as send:
            result = self.engine.dispatch(parse_time("2026-10-17T14:20:00+08:00"), True)
        self.assertTrue(any(job["reason"] == "reminder" and job["status"] == "sent" for job in result))

    def test_deadline_is_displayed_without_an_extra_reminder(self):
        self.ingest(event(deadline_at="2026-10-16T18:00:00+08:00"))
        self.assertEqual(len(self.store.jobs()), 3)
        self.assertIn("报名截止时间：2026-10-16 18:00", self.engine.dispatch(NOW)[0]["body"])

    def test_summary_change_is_a_meaningful_update(self):
        self.ingest(event(summary="讲座介绍研究设计。", evidence={"summary": "介绍研究设计。"}))
        changed = self.ingest(event(summary="讲座介绍研究设计与绘图。", evidence={"summary": "介绍研究设计与绘图。"}), "2")["events"][0]
        self.assertEqual(changed["version"], 2)

    def test_calendar_has_no_attendance_or_venue(self):
        self.ingest(event(summary="讲座摘要内容。", deadline_at="2026-10-16T18:00:00+08:00"))
        data = export_ics(self.store.events()).replace("\r\n ", "")
        self.assertIn("讲座摘要内容", data)
        self.assertIn("报名截止时间", data)
        self.assertNotIn("LOCATION:", data)
        self.assertNotIn("到场", data)

    def test_explicit_unresolved_cutoff_replaces_old_precise_cutoff(self):
        self.ingest(event(deadline_at="2026-10-16T18:00:00+08:00", deadline_text="10月16日18:00"))
        changed = self.ingest(event(deadline_at=None, deadline_text="10月17日", needs_review=True,
                                   schedule_ready=True, category="lecture_update"), "2")["events"][0]
        self.assertIsNone(changed["deadline_at"])
        self.assertIn("10月17日（待核实）", self.engine.dispatch(NOW)[0]["body"])


if __name__ == "__main__":
    unittest.main()
