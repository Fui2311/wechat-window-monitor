from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.capture import CaptureError, expand_forward
from wechat_tracker.config import load_config
from wechat_tracker.engine import Engine, parse_time
from wechat_tracker.store import Store
from wechat_tracker.watcher import enrich_forwards

GROUP = "2026级计算机研究生通知群"
NOW = parse_time("2026-10-15T14:00:00+08:00")
TEXT = "讲座主题：《理论框架建构与绘图》\n主讲嘉宾：余晖\n讲座时间：2026年10月16日19:00-21:00\n讲座地点：125会议厅\n请提前20分钟到达现场进行签到。"


def card(identifier="parent"):
    return dict(id=identifier, group=GROUP, text="聊天记录群聊的聊天记录甲：讲座通知", kind="forward",
                sender=None, sent_at=None, source="windows_uia",
                source_meta=dict(runtime_id=[42, 123, 4], process_id=123, window_handle=456,
                                 fully_visible=True, class_name="mmui::ChatBubbleItemView"))


def native_result(message):
    return dict(ok=True, status="ready", group=GROUP, process_id=123, process_name="Weixin",
                window_title="群聊的聊天记录", window_handle=789, list_count=1,
                observed_at=NOW.isoformat(), history_complete=True, incomplete_reasons=[],
                items=[dict(text=TEXT + " 2026年10月15日 09:34",
                            automation_id="qt_scrollarea_viewport.chat_bubble_item_view")],
                binding=dict(group_window_handle=456, parent_runtime_id=[42, 123, 4],
                             parent_text_sha256=hashlib.sha256(message["text"].encode()).hexdigest(),
                             detail_window_handle=789, new_window_observed=True, restored=True))


def expanded(message, complete=True):
    result = dict(message, children=[dict(id="child", group=GROUP, text=TEXT, kind="text")],
                  incomplete=not complete)
    result["source_meta"] = dict(message["source_meta"], history_complete=complete)
    return result


class AutomaticCaptureTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(group=GROUP, capture=dict(source="windows_uia", auto_expand_forward=True))

    def test_bound_open_preserves_parent_and_original_child_time(self):
        message = card()
        with patch("wechat_tracker.capture._run_uia", return_value=native_result(message)) as run:
            parent = expand_forward(self.config, message)
        self.assertEqual(run.call_args.kwargs, {"mode": "expand"})
        self.assertEqual(parent["id"], message["id"])
        self.assertEqual(parent["text"], message["text"])
        self.assertIsNone(parent["sent_at"])
        self.assertTrue(parent["source_group_verified"])
        self.assertEqual(parent["children"][0]["sent_at"], "2026-10-15T09:34:00+08:00")
        self.assertTrue(parent["children"][0]["source_meta"]["source_group_verified"])
        self.assertFalse(parent["incomplete"])

    def test_rejects_stale_or_forged_open_linkage(self):
        for key, bad in (("parent_runtime_id", [42, 999]), ("group_window_handle", 444),
                         ("parent_text_sha256", "wrong"), ("new_window_observed", False),
                         ("restored", False), ("detail_window_handle", 555)):
            with self.subTest(key=key):
                raw = native_result(card())
                raw["binding"][key] = bad
                with patch("wechat_tracker.capture._run_uia", return_value=raw), self.assertRaises(CaptureError):
                    expand_forward(self.config, card())

    def test_wrong_process_or_group_is_rejected(self):
        raw = native_result(card())
        raw["process_id"] = 999
        with patch("wechat_tracker.capture._run_uia", return_value=raw), self.assertRaises(CaptureError):
            expand_forward(self.config, card())
        with patch("wechat_tracker.capture._run_uia") as run, self.assertRaises(CaptureError):
            expand_forward(self.config, dict(card(), group="其他群"))
        run.assert_not_called()

    def test_unbound_clipped_or_nonforward_input_never_touches_ui(self):
        for change in (dict(source="jsonl"), dict(kind="file"), dict(text="文件 名单.pdf"),
                       dict(source_meta={}), dict(source_meta=dict(card()["source_meta"], fully_visible=False))):
            with self.subTest(change=change), patch("wechat_tracker.capture._run_uia") as run, self.assertRaises(CaptureError):
                expand_forward(self.config, dict(card(), **change))
            run.assert_not_called()

    def test_native_failure_is_explicit_and_not_interpreted_as_empty_history(self):
        raw = dict(ok=False, status="user_interrupted", error="User input changed")
        with patch("wechat_tracker.capture._run_uia", return_value=raw), self.assertRaisesRegex(CaptureError, "user_interrupted"):
            expand_forward(self.config, card())

    def test_missing_scroll_proof_retains_partial_status(self):
        raw = native_result(card())
        raw.update(history_complete=False, incomplete_reasons=["scroll_pattern_unavailable"])
        with patch("wechat_tracker.capture._run_uia", return_value=raw):
            parent = expand_forward(self.config, card())
        self.assertTrue(parent["incomplete"])
        self.assertIn("history_completeness_not_proven", parent["source_meta"]["incomplete_reasons"])
        self.assertIn("scroll_pattern_unavailable", parent["source_meta"]["incomplete_reasons"])

    def test_subprocess_receives_bindings_and_separate_timeout(self):
        raw = native_result(card())
        with patch("wechat_tracker.capture.sys.platform", "win32"), \
             patch("wechat_tracker.capture.shutil.which", return_value="powershell.exe"), \
             patch("wechat_tracker.capture.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(raw), "")) as run:
            expand_forward(self.config, card())
        args = run.call_args.args[0]
        self.assertTrue(any(str(value).endswith("expand-weixin-forward.ps1") for value in args))
        self.assertEqual(args[args.index("-TargetRuntimeId") + 1], "42,123,4")
        self.assertEqual(args[args.index("-GroupWindowHandle") + 1], "456")
        self.assertEqual(run.call_args.kwargs["timeout"], 25)


class ForwardWatcherTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.config = dict(group=GROUP, capture=dict(source="windows_uia", auto_expand_forward=True),
                           reminder_minutes=[1440, 60], email={})
        self.engine = Engine(self.store, self.config)

    def tearDown(self):
        self.store.close()

    def enrich(self, messages=None, **kwargs):
        return enrich_forwards(messages or [card()], self.store, self.config, now=kwargs.pop("now", NOW), **kwargs)

    def test_default_capture_never_opens_anything(self):
        self.config["capture"]["auto_expand_forward"] = False
        with patch("wechat_tracker.watcher.expand_forward") as expand:
            rows, diagnostics = self.enrich(include_existing=True)
        expand.assert_not_called()
        self.assertEqual(rows, [card()])
        self.assertEqual(diagnostics, [])

    def test_initial_baseline_is_not_expanded_on_next_poll(self):
        with patch("wechat_tracker.watcher.expand_forward") as expand:
            rows, _ = self.enrich()
            self.engine.capture_snapshot(rows, now=NOW)
            self.enrich(now=NOW + timedelta(seconds=15))
        expand.assert_not_called()
        self.assertEqual(self.store.events(), [])

    def test_explicit_include_existing_can_expand_a_saved_baseline(self):
        rows, _ = self.enrich()
        self.engine.capture_snapshot(rows, now=NOW)
        with patch("wechat_tracker.watcher.expand_forward", return_value=expanded(card())) as expand:
            rows, diagnostics = self.enrich(include_existing=True)
        expand.assert_called_once()
        self.assertEqual(diagnostics[0]["status"], "complete")
        self.assertEqual(len(self.engine.capture_snapshot(rows, now=NOW)), 1)

    def test_read_extract_queue_and_repeat_are_idempotent(self):
        with patch("wechat_tracker.capture._run_uia", return_value=native_result(card())) as run:
            rows, _ = self.enrich(include_existing=True)
            first = self.engine.capture_snapshot(rows, include_existing=True, now=NOW)
            rows, _ = self.enrich(now=NOW + timedelta(seconds=15))
            second = self.engine.capture_snapshot(rows, now=NOW + timedelta(seconds=15))
        run.assert_called_once()
        self.assertEqual(len(first[0]["events"]), 1)
        self.assertEqual(second, [])
        event = self.store.events()[0]
        self.assertEqual(event["start_at"], "2026-10-16T19:00:00+08:00")
        self.assertNotIn("arrival_at", event)
        reminders = [job for job in self.store.jobs() if job["reason"] == "reminder"]
        self.assertEqual(reminders, [])
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(len(self.engine.dispatch(NOW)), 1)

    def test_cache_survives_database_close_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart.sqlite3"
            store = Store(path)
            with patch("wechat_tracker.watcher.expand_forward", return_value=expanded(card())) as expand:
                enrich_forwards([card()], store, self.config, include_existing=True, now=NOW)
                store.close()
                store = Store(path)
                rows, _ = enrich_forwards([card()], store, self.config, include_existing=True, now=NOW + timedelta(minutes=1))
                store.close()
            expand.assert_called_once()
            self.assertEqual(len(rows[0]["children"]), 1)

    def test_failure_backs_off_and_stops_rest_of_batch(self):
        cards = [card("one"), card("two")]
        with patch("wechat_tracker.watcher.expand_forward", side_effect=CaptureError("user_interrupted")) as expand:
            rows, diagnostics = self.enrich(cards, include_existing=True)
            self.enrich([card("one")], include_existing=True, now=NOW + timedelta(seconds=15))
        expand.assert_called_once()
        self.assertEqual(rows, cards)
        self.assertEqual([d["status"] for d in diagnostics], ["retry", "deferred"])
        with patch("wechat_tracker.watcher.expand_forward", return_value=expanded(card("one"))) as expand:
            self.enrich([card("one")], include_existing=True, now=NOW + timedelta(seconds=31))
        expand.assert_called_once()

    def test_partial_evidence_remains_available_during_failed_reread(self):
        self.config["capture"]["forward_partial_retry_seconds"] = 60
        partial = expanded(card(), complete=False)
        with patch("wechat_tracker.watcher.expand_forward", return_value=partial) as expand:
            self.enrich(include_existing=True)
            self.enrich(include_existing=True, now=NOW + timedelta(seconds=30))
        expand.assert_called_once()
        with patch("wechat_tracker.watcher.expand_forward", side_effect=CaptureError("foreground_changed")):
            rows, _ = self.enrich(include_existing=True, now=NOW + timedelta(seconds=61))
        self.assertEqual(rows[0]["children"], partial["children"])
        with patch("wechat_tracker.watcher.expand_forward") as expand:
            rows, _ = self.enrich(include_existing=True, now=NOW + timedelta(seconds=62))
        expand.assert_not_called()
        self.assertEqual(rows[0]["children"], partial["children"])

    def test_mac_busy_input_defers_without_growing_failure_backoff(self):
        self.config["capture"]["source"] = "macos_ax"
        message = dict(card(), source="macos_ax")
        with patch("wechat_tracker.macos.expand_forward", side_effect=CaptureError("user_busy")) as expand:
            for seconds in (0, 3, 6, 9):
                rows, diagnostics = self.enrich([message], include_existing=True,
                                               now=NOW + timedelta(seconds=seconds))
                cached = self.store.db.execute("SELECT * FROM forward_reads").fetchone()
                self.assertEqual((cached["status"], cached["attempts"]), ("deferred", 0))
                self.assertEqual(parse_time(cached["next_attempt_at"]), NOW + timedelta(seconds=seconds + 3))
                self.assertEqual(diagnostics[0]["status"], "deferred")
                self.assertEqual(rows, [message])
            self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=10))
        self.assertEqual(expand.call_count, 4)
        with patch("wechat_tracker.macos.expand_forward", return_value=expanded(message)) as expand:
            rows, _ = self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=12))
            self.engine.capture_snapshot(rows, include_existing=True, now=NOW + timedelta(seconds=12))
            repeated, _ = self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=15))
            self.assertEqual(self.engine.capture_snapshot(repeated, now=NOW + timedelta(seconds=15)), [])
        expand.assert_called_once()
        self.assertEqual(len(self.store.events()), 1)
        self.assertEqual(len(self.store.jobs()), 1)

    def test_mac_read_errors_keep_failure_backoff_and_stop_the_batch(self):
        self.config["capture"]["source"] = "macos_ax"
        for reason in ("card_occluded", "parent_changed"):
            with self.subTest(reason=reason):
                message = dict(card(reason), source="macos_ax")
                other = dict(card(reason + "-other"), source="macos_ax")
                with patch("wechat_tracker.macos.expand_forward", side_effect=CaptureError(reason)) as expand:
                    _, diagnostics = self.enrich([message, other], include_existing=True)
                    self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=15))
                expand.assert_called_once()
                self.assertEqual([d["status"] for d in diagnostics], ["retry", "deferred"])
                cached = self.store.db.execute("SELECT * FROM forward_reads WHERE parent_id=?", (reason,)).fetchone()
                self.assertEqual((cached["status"], cached["attempts"]), ("retry", 1))
                self.assertEqual(parse_time(cached["next_attempt_at"]), NOW + timedelta(seconds=30))

    def test_mac_only_explicit_postpone_waits_five_minutes_activity_retries_when_idle(self):
        for reason in ("user_postponed", "user_active_during_notice", "user_interrupted"):
            with self.subTest(reason=reason):
                with self.store.db:
                    self.store.db.execute("DELETE FROM forward_reads")
                message = dict(card(reason), source="macos_ax")
                delay = 300 if reason == "user_postponed" else 3
                with patch("wechat_tracker.macos.expand_forward", side_effect=CaptureError(reason)) as expand:
                    _, diagnostics = self.enrich([message], include_existing=True)
                    self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=delay - 1))
                    expand.assert_called_once()
                    self.enrich([message], include_existing=True, now=NOW + timedelta(seconds=delay))
                    self.assertEqual(expand.call_count, 2)
                cached = self.store.db.execute("SELECT * FROM forward_reads WHERE parent_id=?", (reason,)).fetchone()
                self.assertEqual((cached["status"], cached["attempts"]), ("deferred", 0))
                self.assertEqual(diagnostics[0]["status"], "deferred")

    def test_mac_postpone_pauses_other_new_cards_in_the_same_group_across_restart(self):
        message = dict(card("postponed"), source="macos_ax")
        other = dict(card("new-after-postpone"), source="macos_ax")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.sqlite3"
            store = Store(database)
            with patch("wechat_tracker.macos.expand_forward", side_effect=CaptureError("user_postponed")):
                enrich_forwards([message], store, self.config, include_existing=True, now=NOW)
            store.close()
            store = Store(database)
            try:
                with patch("wechat_tracker.macos.expand_forward", return_value=expanded(other)) as expand:
                    rows, diagnostics = enrich_forwards([other], store, self.config, include_existing=True,
                                                        now=NOW + timedelta(seconds=299))
                    expand.assert_not_called()
                    self.assertEqual(rows, [other])
                    self.assertEqual(diagnostics[0]["status"], "deferred")
                    enrich_forwards([other], store, self.config, include_existing=True,
                                    now=NOW + timedelta(seconds=300))
                    expand.assert_called_once()
            finally:
                store.close()

    def test_mac_legacy_partial_cache_is_reused_after_restart_even_after_failed_reread(self):
        message = dict(card("legacy"), source="macos_ax")
        partial = expanded(message, complete=False)
        # An old installation may have scheduled a retry, or kept useful body
        # evidence while marking a failed retry. Both must remain read-once.
        for status in ("partial", "retry"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "tracker.sqlite3"
                old = Store(database)
                with old.db:
                    old.db.execute("INSERT INTO forward_reads VALUES (?,?,?,?,?,?,?)",
                                   (GROUP, message["id"], status, json.dumps(partial), 2,
                                    "2026-10-15T00:00:00+00:00", "detail_open_timeout"))
                old.close()
                reopened = Store(database)
                try:
                    with patch("wechat_tracker.macos.expand_forward") as expand:
                        rows, diagnostics = enrich_forwards([message], reopened, self.config,
                                                           include_existing=True, now=NOW + timedelta(days=1))
                    expand.assert_not_called()
                    self.assertEqual(rows[0]["children"], partial["children"])
                    self.assertEqual(diagnostics, [])
                    self.assertTrue(rows[0]["incomplete"])
                finally:
                    reopened.close()

    def test_per_poll_card_limit_does_not_drop_remaining_cards(self):
        self.config["capture"]["forward_max_cards"] = 1
        with patch("wechat_tracker.watcher.expand_forward", side_effect=lambda config, row: expanded(row)) as expand:
            rows, diagnostics = self.enrich([card("one"), card("two")], include_existing=True)
        expand.assert_called_once()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("children", rows[1])
        self.assertEqual(diagnostics[1]["status"], "deferred")

    def test_mixed_group_batch_is_rejected_before_first_ui_action(self):
        with patch("wechat_tracker.watcher.expand_forward") as expand, self.assertRaises(ValueError):
            self.enrich([card(), dict(card("bad"), group="其他群")], include_existing=True)
        expand.assert_not_called()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM forward_reads").fetchone()[0], 0)

    def test_nested_observation_changes_do_not_create_new_messages_or_jobs(self):
        message = expanded(card())
        self.engine.ingest(message, NOW)
        other = deepcopy(message)
        other["observed_at"] = "2026-10-15T16:00:00+08:00"
        other["children"][0]["observed_at"] = other["observed_at"]
        other["children"][0]["source_meta"] = {"runtime_id": [42, 999]}
        self.assertTrue(self.engine.ingest(other, NOW)["duplicate"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(len(self.store.jobs()), 1)

    def test_invalid_automation_configuration_is_rejected(self):
        for setting in ("auto_expand_forward = 'true'", "forward_max_cards = 0", "forward_max_pages = 21",
                        "forward_idle_seconds = 0", "forward_partial_retry_seconds = 1", "forward_timeout_seconds = 61",
                        "forward_notice_seconds = 0", "forward_notice_seconds = 16", "forward_postpone_seconds = 5",
                        "mac_forward_interaction = 'unrestricted'", "mac_forward_card_side = 'guess'"):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                path.write_text(f'group = "{GROUP}"\n[capture]\n{setting}\n', encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(f'group = "{GROUP}"\n[capture]\nmac_forward_interaction = "activate_click"\n'
                            'forward_notice_seconds = 10\nforward_timeout_seconds = 15\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
