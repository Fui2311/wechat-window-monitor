import copy
import tempfile
import unittest
from unittest.mock import patch

from test_monitor_core import FakeCapture, FakeModel, local_rule
from wechat_monitor.application import Application
from wechat_monitor.capture import ExactWindowCapture
from wechat_monitor.config import window_config


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.capture, self.model = FakeCapture(), FakeModel()
        self.app = Application(self.temp.name, model=self.model, capture=self.capture)
        self.addCleanup(self.app.close)
        self.window = self.app.save_window(
            dict(
                name="测试",
                target="构建失败",
                mode="local",
                include_existing=True,
                capture={"source": "windows_uia", "auto_expand_forward": True},
            )
        )
        rule = self.app.save_rule(self.window["id"], local_rule())
        self.app.activate(self.window["id"], rule["id"])
        self.window = self.app.require_window(self.window["id"])
        self.card = dict(id="card-1", group="测试", kind="forward", text="构建失败预览")
        self.app.repo.ingest(self.window, [self.card])

    def records(self):
        return self.app.repo.records(self.window["id"])["records"]

    def test_body_persisted_and_title_excluded_without_replay(self):
        body = dict(
            self.card, children=[dict(text="构建成功", group="测试")], incomplete=True
        )
        self.capture.expand = lambda *args: copy.deepcopy(body)
        self.app.runtime.process_pending(self.window)
        record = self.records()[0]
        self.assertEqual(record["state"], "processed")
        self.assertFalse(record["result"]["matched"])
        self.assertEqual(record["payload"]["children"], body["children"])
        self.app.repo.ingest(self.window, [self.card])
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.app.repo.pending(self.window["id"]), [])

    def test_disabled_or_failed_expansion_does_not_call_model(self):
        window = copy.deepcopy(self.window)
        window["capture"]["auto_expand_forward"] = False
        self.app.runtime.process_pending(window)
        self.assertEqual(self.records()[0]["state"], "error")
        self.assertIn("正文未读取", self.records()[0]["error"])
        self.assertIsNone(self.records()[0]["result"])
        self.app.repo.retry(self.window["id"])

        def fail(*args):
            raise RuntimeError("private native output")

        self.capture.expand = fail
        self.app.runtime.process_pending(self.window)
        self.assertIn("读取失败", self.records()[0]["error"])
        self.assertNotIn("private", self.records()[0]["error"])
        self.assertEqual(self.model.calls, [])

    def test_wrong_group_body_not_persisted(self):
        self.capture.expand = lambda *args: dict(
            self.card, children=[dict(group="其他群", text="构建失败")]
        )
        self.app.runtime.process_pending(self.window)
        self.assertEqual(self.records()[0]["state"], "error")
        self.assertNotIn("children", self.records()[0]["payload"])

    def test_nested_unread_title_is_not_evidence(self):
        self.capture.expand = lambda *args: dict(
            self.card, children=[dict(kind="forward", text="构建失败")]
        )
        self.app.runtime.process_pending(self.window)
        self.assertEqual(self.records()[0]["state"], "error")
        self.assertIn("嵌套", self.records()[0]["error"])

    def test_old_record_can_be_explicitly_requeued_only_in_its_window(self):
        self.capture.expand = lambda *args: dict(
            self.card, children=[dict(text="构建失败")]
        )
        self.app.runtime.process_pending(self.window)
        record = self.records()[0]
        with self.assertRaises(ValueError):
            self.app.repo.retry_forward("wrong-window", record["id"])
        self.app.repo.retry_forward(self.window["id"], record["id"])
        self.assertEqual(self.records()[0]["state"], "pending")

    def test_binding_is_refreshed_before_click(self):
        capture = ExactWindowCapture()
        fresh = dict(self.card, source="macos_ax")
        window = copy.deepcopy(self.window)
        window["capture"]["source"] = "macos_ax"
        with (
            patch("wechat_monitor.capture.capture_once", return_value=[fresh]),
            patch(
                "wechat_tracker.macos.expand_forward",
                return_value={"children": [{"text": "body"}]},
            ) as expand,
        ):
            capture.expand(window, self.card)
            self.assertEqual(expand.call_args.args[1], fresh)
            self.assertEqual(
                expand.call_args.args[0]["capture"]["forward_timeout_seconds"], 45
            )
        with patch("wechat_monitor.capture.capture_once", return_value=[]):
            with self.assertRaises(ValueError):
                capture.expand(window, self.card)

    def test_forward_flags_are_validated(self):
        base = dict(
            name="a",
            target="b",
            capture=dict(
                source="macos_ax",
                auto_expand_forward=True,
                mac_forward_card_side="left",
            ),
        )
        self.assertTrue(window_config(base)["capture"]["auto_expand_forward"])
        for change in (
            {"auto_expand_forward": "yes"},
            {"mac_forward_card_side": "guess"},
        ):
            with self.assertRaises(ValueError):
                window_config(dict(base, capture=dict(base["capture"], **change)))
