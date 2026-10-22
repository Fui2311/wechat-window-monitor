"""Generic recognition, lifecycle and persistence use only synthetic data."""

import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from wechat_monitor.application import Application
from wechat_monitor.capture import native_config
from wechat_monitor.recognition import RuleGenerator, LocalCore, SemanticCore
from wechat_monitor.recognition.schema import validate_rule


def local_rule(target="构建失败"):
    return dict(
        schema_version=1,
        mode="local",
        target=target,
        instructions="关键词规则",
        match={
            "any": [dict(field="text", op="contains", value="构建失败")],
            "all": [],
            "none": [],
        },
    )


class FakeModel:
    def __init__(self):
        self.calls = []
        self.output = {
            "instructions": "仅判断明确项目构建失败，成功和询问不命中",
            "match": local_rule()["match"],
        }

    def complete(self, system, data):
        self.calls.append((system, copy.deepcopy(data)))
        return copy.deepcopy(self.output)


class FakeCapture:
    def __init__(self):
        self.messages = []
        self.fail = False

    def read(self, window):
        if self.fail:
            raise RuntimeError("private upstream detail must never leak")
        return copy.deepcopy(self.messages)

    def probe(self, window):
        return dict(
            ok=True,
            status="ready",
            group=window["name"],
            message_count=len(self.messages),
        )


class MonitorCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = FakeModel()
        self.capture = FakeCapture()
        self.app = Application(self.temp.name, model=self.model, capture=self.capture)
        self.addCleanup(self.app.close)
        self.window = self.app.save_window(
            dict(
                name="文件传输助手",
                target="构建失败",
                mode="local",
                include_existing=True,
                interval=1,
                capture={"source": "windows_uia"},
            )
        )
        self.identity = self.window["id"]

    def activate(self):
        draft = self.app.save_rule(self.identity, local_rule())
        self.app.activate(self.identity, draft["id"])
        self.window = self.app.require_window(self.identity)
        return draft

    def test_generate_once_persist_local_execution_never_calls_model(self):
        draft = self.app.generate(self.identity)
        self.assertEqual(len(self.model.calls), 1)
        self.assertIsNone(self.app.require_window(self.identity)["active_rule"])
        self.app.activate(self.identity, draft["id"])
        self.window = self.app.require_window(self.identity)
        self.app.repo.ingest(
            self.window,
            [dict(id="1", text="构建失败：服务器异常", group="文件传输助手")],
        )
        self.app.runtime.process_pending(self.window)
        self.assertEqual(len(self.model.calls), 1)
        record = self.app.repo.records(self.identity)["records"][0]
        self.assertTrue(record["result"]["matched"])
        self.assertEqual(record["rule_id"], draft["id"])
        reopened = Application(self.temp.name, model=self.model, capture=self.capture)
        try:
            self.assertEqual(
                reopened.require_window(self.identity)["active_rule"], draft["id"]
            )
            self.assertEqual(reopened.windows()[0]["runtime"]["status"], "stopped")
        finally:
            reopened.close()

    def test_semantic_reuses_saved_rule_and_requires_verbatim_evidence(self):
        rule = dict(local_rule(), mode="llm", instructions="明确构建失败才提醒")
        self.model.output = {
            "matched": True,
            "reason": "项目失败",
            "evidence": "构建失败",
        }
        core = SemanticCore(self.model)
        for _ in range(2):
            self.assertTrue(core.recognize({"text": "刚刚构建失败"}, rule)["matched"])
        self.assertEqual(
            self.model.calls[0][1]["saved_rule"], self.model.calls[1][1]["saved_rule"]
        )
        self.model.output["evidence"] = "不存在的原文"
        with self.assertRaises(ValueError):
            core.recognize({"text": "刚刚构建失败"}, rule)

    def test_local_nested_content_unicode_and_negative_clauses(self):
        rule = local_rule()
        rule["match"]["none"] = [dict(field="sender", op="equals", value="bot")]
        self.assertTrue(
            LocalCore().recognize(dict(children=[{"text": "构建失败"}]), rule)[
                "matched"
            ]
        )
        self.assertFalse(
            LocalCore().recognize(dict(sender="BOT", text="构建失败"), rule)["matched"]
        )
        self.assertTrue(
            LocalCore().recognize(dict(sender=None, text="构建失败"), rule)["matched"]
        )
        rule["match"]["any"][0]["op"] = "exec"
        with self.assertRaises(ValueError):
            validate_rule(rule)
        with self.assertRaises(ValueError):
            validate_rule(dict(local_rule(), match=dict(any=[], all=[], none=[])))

    def test_wrong_window_frame_is_rejected_before_writes(self):
        self.activate()
        with self.assertRaises(ValueError):
            self.app.repo.ingest(
                self.window,
                [{"text": "构建失败"}, {"text": "构建失败", "group": "另一窗口"}],
            )
        self.assertEqual(self.app.repo.records(self.identity)["records"], [])
        self.assertEqual(native_config(self.window)["group"], "文件传输助手")
        self.assertFalse(native_config(self.window)["capture"]["recover_window"])
        config = self.window_config() | {"name": "  精确标题  "}
        spaced = self.app.save_window(config)
        self.assertEqual(native_config(spaced)["group"], "  精确标题  ")

    def test_first_baseline_and_new_capture_are_deduplicated(self):
        self.window = self.app.save_window(
            dict(self.window_config(), include_existing=False, revision=1),
            self.identity,
        )
        self.activate()
        self.app.repo.ingest(self.window, [{"text": "旧的构建失败", "id": "old"}])
        self.app.repo.ingest(
            self.window,
            [
                {"text": "旧的构建失败", "id": "old"},
                {"text": "新的构建失败", "id": "new"},
            ],
        )
        self.app.runtime.process_pending(self.window)
        self.assertEqual(
            self.app.repo.counts(self.identity),
            dict(baseline=1, processed=1, matched=1),
        )

    def window_config(self):
        return {
            k: self.window[k]
            for k in (
                "name",
                "target",
                "mode",
                "interval",
                "include_existing",
                "capture",
            )
        }

    def test_rule_versions_stale_target_and_optimistic_settings(self):
        first = self.activate()
        current = self.app.require_window(self.identity)
        body = dict(
            self.window_config(), target="提醒招生通知", revision=current["revision"]
        )
        changed = self.app.save_window(body, self.identity)
        self.assertIsNone(changed["active_rule"])
        with self.assertRaises(ValueError):
            self.app.activate(self.identity, first["id"])
        with self.assertRaises(ValueError):
            self.app.save_window(body, self.identity)
        self.assertEqual(self.app.repo.rules(self.identity)[0]["spec"], local_rule())

    def test_test_path_never_stores_or_replaces_rule(self):
        result = self.app.test(self.identity, local_rule(), dict(text="项目构建失败"))
        self.assertTrue(result["matched"])
        self.assertEqual(self.app.repo.records(self.identity)["records"], [])
        self.assertEqual(self.app.repo.rules(self.identity), [])

    def test_runtime_recovers_from_capture_error_and_stops(self):
        self.activate()
        self.capture.fail = True
        self.app.start(self.identity)
        for _ in range(100):
            if self.app.runtime.status(self.identity)["status"] == "waiting":
                break
            time.sleep(0.01)
        self.assertEqual(self.app.runtime.status(self.identity)["status"], "waiting")
        with self.assertRaises(ValueError):
            self.app.save_window(dict(self.window_config(), revision=2), self.identity)
        self.capture.fail = False
        self.capture.messages = [dict(text="构建失败", id="recover")]
        for _ in range(200):
            if self.app.repo.counts(self.identity).get("processed"):
                break
            time.sleep(0.01)
        self.assertEqual(self.app.repo.counts(self.identity).get("matched"), 1)
        self.assertEqual(
            self.app.runtime.stop(self.identity, wait=2)["status"], "stopped"
        )

    def test_generation_race_does_not_save_old_target(self):
        started = threading.Event()
        finish = threading.Event()
        errors = []
        original = self.model.complete

        def delayed(*args):
            started.set()
            finish.wait(2)
            return original(*args)

        self.model.complete = delayed

        def generate():
            try:
                self.app.generate(self.identity)
            except ValueError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=generate)
        thread.start()
        self.assertTrue(started.wait(1))
        self.app.save_window(
            dict(self.window_config(), target="新目标", revision=1), self.identity
        )
        finish.set()
        thread.join(2)
        self.assertTrue(errors)
        self.assertEqual(self.app.repo.rules(self.identity), [])

    def test_cursor_paging_never_repeats_new_arrivals_and_is_window_scoped(self):
        self.activate()
        self.app.repo.ingest(
            self.window, [dict(id=str(i), text="构建失败") for i in range(8)]
        )
        page = self.app.repo.records(self.identity, limit=3)
        self.app.repo.ingest(self.window, [dict(id="arrival", text="新到构建失败")])
        second = self.app.repo.records(
            self.identity, limit=3, before=page["next_before"]
        )
        self.assertFalse(
            {r["id"] for r in page["records"]} & {r["id"] for r in second["records"]}
        )
        other = self.app.save_window(dict(self.window_config(), name="另一个私聊窗口"))
        self.assertEqual(self.app.repo.records(other["id"])["records"], [])

    def test_settings_never_return_key_and_reject_remote_http(self):
        with self.assertRaises(ValueError):
            self.app.settings.save(dict(base_url="http://remote.example/v1"))
        with patch.dict("os.environ", {}, clear=True):
            public = self.app.settings.save(
                dict(model="test", api_key="synthetic-only-token")
            )
            self.assertTrue(public["key_configured"])
            self.assertNotIn("synthetic-only-token", json.dumps(public))
            self.assertEqual(self.app.settings.key_path.stat().st_mode & 0o777, 0o600)


class WorkspaceLeaseTests(unittest.TestCase):
    def test_os_lock_rejects_second_process_owner_and_releases(self):
        from wechat_monitor.locking import WorkspaceLock

        with tempfile.TemporaryDirectory() as root:
            with WorkspaceLock(root):
                with self.assertRaises(ValueError):
                    WorkspaceLock(root)
            with WorkspaceLock(root):
                pass


class RetryEvidenceTests(unittest.TestCase):
    def test_semantic_failure_is_preserved_then_explicitly_retried(self):
        with tempfile.TemporaryDirectory() as root:
            model = FakeModel()
            app = Application(root, model=model, capture=FakeCapture())
            try:
                window = app.save_window(
                    dict(
                        name="精确私聊",
                        target="构建失败",
                        mode="llm",
                        include_existing=True,
                        capture={"source": "windows_uia"},
                    )
                )
                rule = app.save_rule(
                    window["id"],
                    dict(local_rule(), mode="llm", instructions="明确构建失败才提醒"),
                )
                app.activate(window["id"], rule["id"])
                window = app.require_window(window["id"])
                app.repo.ingest(window, [{"id": "1", "text": "构建失败"}])
                model.output = {
                    "matched": True,
                    "reason": "bad",
                    "evidence": "编造证据",
                }
                app.runtime.process_pending(window)
                record = app.repo.records(window["id"])["records"][0]
                self.assertEqual(record["state"], "error")
                self.assertIsNone(record["result"])
                self.assertEqual(record["payload"]["text"], "构建失败")
                app.runtime.process_pending(window)
                self.assertEqual(len(model.calls), 1)
                self.assertEqual(app.repo.retry(window["id"]), 1)
                model.output = {
                    "matched": True,
                    "reason": "明确失败",
                    "evidence": "构建失败",
                }
                app.runtime.process_pending(window)
                self.assertEqual(app.repo.counts(window["id"]).get("matched"), 1)
                self.assertEqual(
                    app.repo.records(window["id"])["records"][0]["rule_id"], rule["id"]
                )
            finally:
                app.close()
