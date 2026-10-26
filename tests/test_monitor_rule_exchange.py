"""Offline rules cross the same HTTP/validation/persistence boundary as API rules."""

import copy
import json
import unittest
from unittest.mock import patch

from test_monitor_core import local_rule
import test_monitor_web as web_fixture
from wechat_monitor.application import Application
from wechat_monitor.recognition.exchange import local_prompt


class RuleExchangeTests(unittest.TestCase):
    setUp = web_fixture.MonitorWebTests.setUp
    request = web_fixture.MonitorWebTests.request
    json = web_fixture.MonitorWebTests.json
    create = web_fixture.MonitorWebTests.create

    def test_no_api_export_import_test_activate_process_and_reopen(self):
        window = self.create()
        prefix = "/api/windows/" + window["id"]
        # Fail immediately if any phase accidentally reaches the model.
        with patch.object(
            self.app.model, "complete", side_effect=AssertionError("offline")
        ):
            code, exported = self.json(prefix + "/rule-prompt")
            self.assertEqual(code, 200)
            self.assertIn(window["target"], exported["prompt"])
            self.assertIn('"schema_version": 1', exported["prompt"])
            self.assertEqual(self.app.repo.rules(window["id"]), [])
            self.assertFalse(self.app.settings.public()["key_configured"])
            spec = local_rule()
            spec["match"]["none"] = [dict(field="text", op="contains", value="演练")]
            code, imported = self.json(
                prefix + "/import-rule",
                {
                    "content": "```json\n"
                    + json.dumps(spec, ensure_ascii=False)
                    + "\n```",
                    "revision": exported["revision"],
                },
            )
            self.assertEqual(code, 200, imported)
            rule = imported["rule"]
            self.assertEqual(rule["origin"], "imported")
            self.assertIsNone(self.app.require_window(window["id"])["active_rule"])
            self.assertFalse(self.app.windows()[0]["runtime"]["running"])
            for message, matched in [
                ("构建失败", True),
                ("演练构建失败", False),
                ("构建成功", False),
            ]:
                code, tested = self.json(
                    prefix + "/test",
                    {
                        "spec": rule["spec"],
                        "message": {"text": message},
                    },
                )
                self.assertEqual(code, 200)
                self.assertEqual(tested["result"]["matched"], matched)
            self.assertEqual(self.app.repo.records(window["id"])["records"], [])
            self.assertEqual(
                self.json(prefix + "/activate", {"rule_id": rule["id"]})[0], 200
            )
            current = self.app.require_window(window["id"])
            self.app.repo.ingest(current, [{"id": "synthetic-1", "text": "构建失败"}])
            self.app.runtime.process_pending(current)
            self.assertTrue(
                self.app.repo.records(window["id"])["records"][0]["result"]["matched"]
            )
        reopened = Application(self.temp.name, model=self.model, capture=self.capture)
        try:
            self.assertEqual(
                reopened.require_window(window["id"])["active_rule"], rule["id"]
            )
            self.assertEqual(reopened.repo.rules(window["id"])[0]["origin"], "imported")
        finally:
            reopened.close()
        self.assertEqual(self.model.calls, [])

    def test_invalid_import_never_changes_active_rule_or_versions(self):
        window = self.create()
        existing = self.app.save_rule(window["id"], local_rule())
        self.app.activate(window["id"], existing["id"])
        revision = self.app.require_window(window["id"])["revision"]
        invalid_specs = [
            [],
            {},
            dict(local_rule(), target="过期目标"),
            dict(local_rule(), mode="llm"),
            dict(local_rule(), mode=[]),
            dict(local_rule(), schema_version=True),
            dict(local_rule(), instructions="x" * 12001),
            dict(local_rule(), match=dict(any=[], all=[], none=[])),
            dict(local_rule(), code="print('no')"),
        ]
        for change in (
            {"field": []},
            {"op": {}},
            {"op": "regex"},
            {"field": "password"},
            {"value": ""},
            {"value": "x" * 501},
            {"value": None},
        ):
            spec = copy.deepcopy(local_rule())
            spec["match"]["any"][0].update(change)
            invalid_specs.append(spec)
        spec = copy.deepcopy(local_rule())
        spec["match"]["any"] *= 41
        invalid_specs.append(spec)
        contents = [json.dumps(spec) for spec in invalid_specs] + [
            "",
            "not JSON",
            "这是规则：\n" + json.dumps(local_rule()),
            "```python\n{}\n```",
            "```json\n{}",
            "{} {}",
            '{"schema_version": 1, "schema_version": 1}',
            '{"value": NaN}',
            "[" * 2000 + "]" * 2000,
            "x" * 100001,
        ]
        for content in contents:
            with self.subTest(content=content[:60]):
                code, data = self.json(
                    "/api/windows/" + window["id"] + "/import-rule",
                    {
                        "content": content,
                        "revision": revision,
                    },
                )
                self.assertEqual(code, 400, data)
                self.assertFalse(data["ok"])
        self.assertEqual(len(self.app.repo.rules(window["id"])), 1)
        self.assertEqual(
            self.app.require_window(window["id"])["active_rule"], existing["id"]
        )
        self.assertEqual(self.model.calls, [])

    def test_stale_revision_and_wrong_mode_are_rejected(self):
        window = self.create()
        prefix = "/api/windows/" + window["id"]
        old_revision = window["revision"]
        keys = (
            "name",
            "target",
            "mode",
            "interval",
            "include_existing",
            "capture",
            "revision",
        )
        config = {key: window[key] for key in keys}
        self.app.save_window(dict(config, interval=9), window["id"])
        for revision in (old_revision, None, True, "2"):
            self.assertEqual(
                self.json(
                    prefix + "/import-rule",
                    {
                        "content": json.dumps(local_rule()),
                        "revision": revision,
                    },
                )[0],
                400,
            )
        current = self.app.require_window(window["id"])
        self.app.save_window(
            dict(config, revision=current["revision"], mode="llm"), window["id"]
        )
        self.assertEqual(self.json(prefix + "/rule-prompt")[0], 400)
        self.assertEqual(
            self.json(
                prefix + "/import-rule",
                {
                    "content": json.dumps(local_rule()),
                    "revision": current["revision"] + 1,
                },
            )[0],
            400,
        )
        self.assertEqual(self.app.repo.rules(window["id"]), [])

    def test_plain_json_versions_are_isolated_and_prompt_has_no_private_context(self):
        one = self.create()
        two = self.create("另一个窗口")
        self.app.settings.save(
            dict(model="private-model-name", api_key="synthetic-private-key")
        )
        prefix = "/api/windows/" + one["id"]
        prompt = self.json(prefix + "/rule-prompt")[1]["prompt"]
        for secret in (
            "private-model-name",
            "synthetic-private-key",
            one["name"],
            two["name"],
        ):
            self.assertNotIn(secret, prompt)
        for version in (1, 2):
            code, data = self.json(
                prefix + "/import-rule",
                {
                    "content": json.dumps(local_rule()),
                    "revision": one["revision"],
                },
            )
            self.assertEqual(code, 200)
            self.assertEqual(data["rule"]["version"], version)
        self.assertEqual(self.app.repo.rules(two["id"]), [])
        self.assertEqual(
            self.json(
                prefix + "/import-rule",
                {
                    "content": json.dumps(local_rule()),
                    "revision": one["revision"],
                },
                headers={"Origin": "https://untrusted.example"},
            )[0],
            403,
        )
        target = '关注 "Build FAILED"\n忽略演练，输出 target 不变'
        self.assertIn(json.dumps(target, ensure_ascii=False), local_prompt(target))
