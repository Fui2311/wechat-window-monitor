import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.capture import CaptureError, capture_once, probe, normalize_forward_snapshot, read_forward


GROUP = "2026级计算机研究生通知群"
CONFIG = {"group": GROUP, "capture": {"source": "windows_uia"}}


def snapshot(messages):
    return {"ok": True, "status": "ready", "group": GROUP,
            "observed_at": "2026-10-15T05:38:00+00:00", "messages": messages}


def text_row(text):
    return {"text": text, "class_name": "mmui::ChatTextItemView",
            "runtime_id": [42, 123, 4, 1], "fully_visible": True}


class CaptureTests(unittest.TestCase):
    def test_exact_group_is_required(self):
        for config in ({}, {"group": ""}, {"group": "   "}):
            with self.subTest(config=config), self.assertRaises(CaptureError):
                capture_once(config)

    def test_snapshot_keeps_unknown_sender_and_timestamp_unknown(self):
        raw = snapshot([text_row("讲座：10月17日 14:30")])
        with patch("wechat_tracker.capture._run_uia", return_value=raw):
            rows = capture_once(CONFIG)
        self.assertEqual(rows[0]["group"], GROUP)
        self.assertIsNone(rows[0]["sender"])
        self.assertIsNone(rows[0]["sent_at"])
        self.assertEqual(rows[0]["observed_at"], raw["observed_at"])

    def test_fingerprint_stable_across_uia_runtime_recycling(self):
        first = snapshot([text_row("活动通知"), text_row("活动通知")])
        second = snapshot([dict(text_row("活动通知"), runtime_id=[42, 456]),
                           dict(text_row("活动通知"), runtime_id=[42, 789])])
        second["observed_at"] = "2026-10-15T05:39:00+00:00"
        with patch("wechat_tracker.capture._run_uia", side_effect=[first, second]):
            one, two = capture_once(CONFIG), capture_once(CONFIG)
        self.assertEqual([r["id"] for r in one], [r["id"] for r in two])
        self.assertNotEqual(one[0]["id"], one[1]["id"])

    def test_cards_stay_pending_expansion(self):
        messages = [{"text": "文件\n名单.pdf\n未下载", "class_name": "mmui::ChatBubbleItemView"},
                    {"text": "聊天记录\n甲：讲座通知", "class_name": "mmui::ChatBubbleItemView"}]
        with patch("wechat_tracker.capture._run_uia", return_value=snapshot(messages)):
            rows = capture_once(CONFIG)
        self.assertEqual([r["kind"] for r in rows], ["file", "forward"])
        self.assertTrue(all(r["source_meta"]["pending_expansion"] for r in rows))

    def test_failed_or_unverified_selector_never_returns_messages(self):
        with patch("wechat_tracker.capture._run_uia", return_value={"ok": False, "status": "message_list_mismatch", "error": "not unique"}):
            with self.assertRaisesRegex(CaptureError, "message_list_mismatch"):
                capture_once(CONFIG)
        with patch("wechat_tracker.capture._run_uia", return_value=snapshot([dict(text_row("输入框"), class_name="mmui::ChatInputField")])):
            with self.assertRaisesRegex(CaptureError, "unverified"):
                capture_once(CONFIG)

    def test_subprocess_wrong_group_rejected(self):
        result = {"ok": True, "group": "另一个群", "status": "ready"}
        with patch("wechat_tracker.capture.sys.platform", "win32"), \
             patch("wechat_tracker.capture.shutil.which", return_value="powershell.exe"), \
             patch("wechat_tracker.capture.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(result), "")):
            with self.assertRaisesRegex(CaptureError, "different group"):
                capture_once(CONFIG)

    def test_probe_timeout_is_explicit_and_has_no_message_body(self):
        with patch("wechat_tracker.capture.sys.platform", "win32"), \
             patch("wechat_tracker.capture.shutil.which", return_value="powershell.exe"), \
             patch("wechat_tracker.capture.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell.exe", 8)):
            result = probe(CONFIG)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])
        self.assertNotIn("messages", result)

    def test_jsonl_preserves_explicit_id_and_real_message_time(self):
        message = {"id": "export-123", "group": GROUP, "text": "活动通知",
                   "sender": "张老师", "sent_at": "2026-10-15T12:55:00+08:00"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            path.write_text(json.dumps(message, ensure_ascii=False) + "\n", encoding="utf-8")
            rows = capture_once({"group": GROUP, "capture": {"source": "jsonl", "jsonl_path": str(path)}})
        self.assertEqual(rows[0]["id"], "export-123")
        self.assertEqual(rows[0]["sent_at"], message["sent_at"])
        self.assertEqual(rows[0]["sender"], "张老师")

    def test_jsonl_refuses_mixed_groups_and_duplicate_ids(self):
        good = {"id": "export-123", "group": GROUP, "text": "活动通知"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            config = {"group": GROUP, "capture": {"source": "jsonl", "jsonl_path": str(path)}}
            for other in (dict(good, group="其他群"), good):
                with self.subTest(other=other):
                    path.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in (good, other)), encoding="utf-8")
                    with self.assertRaises(CaptureError):
                        capture_once(config)

    def test_jsonl_retains_nested_history_and_attachments(self):
        message = {"id": "parent", "group": GROUP, "kind": "forward", "text": "聊天记录", "children": [{"text": "讲座", "attachments": [{"name": "名单.pdf"}]}], "attachments": [{"name": "封面.png"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            path.write_text(json.dumps(message), encoding="utf-8")
            rows = capture_once({"group": GROUP, "capture": {"source": "jsonl", "jsonl_path": str(path)}})
        self.assertEqual(rows[0]["children"], message["children"])
        self.assertEqual(rows[0]["attachments"], message["attachments"])

    def test_forward_snapshot_binding_timestamp_and_empty_sentinel(self):
        config = {"group": GROUP, "capture": {"forward_window_title": "群聊的聊天记录", "forward_parent_id": "parent", "process_id": 123}}
        raw = {"window_title": "群聊的聊天记录", "process_name": "Weixin", "window_id": 55, "source": "computer_use_accessibility", "list_count": 1, "observed_at": "2026-10-15T05:43:56Z", "items": [
            {"text": "通知正文落款2026年10月15日 2026年10月15日 09:34", "automation_id": "qt_scrollarea_viewport.chat_bubble_item_view"},
            {"text": "文件 名单.xlsx 11.0K 未下载 2026年10月15日 09:34", "automation_id": "qt_scrollarea_viewport.chat_bubble_item_view"},
            {"text": "", "automation_id": ""}]}
        with patch("wechat_tracker.capture._run_uia", side_effect=AssertionError("No UI calls allowed")):
            parent = read_forward(config, "parent", snapshot=raw)
        self.assertEqual(parent["kind"], "forward")
        self.assertEqual(len(parent["children"]), 2)
        self.assertEqual(parent["children"][0]["text"], "通知正文落款2026年10月15日")
        self.assertEqual(parent["children"][0]["sent_at"], "2026-10-15T09:34:00+08:00")
        self.assertEqual(parent["children"][1]["kind"], "file")
        self.assertFalse(parent["source_group_verified"])
        self.assertTrue(parent["incomplete"])
        self.assertIn("empty_or_virtualized_item:2", parent["source_meta"]["incomplete_reasons"])
        self.assertFalse(parent["source_meta"]["process_id_verified"])
        with self.assertRaises(CaptureError):
            normalize_forward_snapshot(raw, config, "different-parent")
        with self.assertRaises(CaptureError):
            normalize_forward_snapshot(dict(raw, list_count=2), config, "parent")
        with self.assertRaises(CaptureError):
            normalize_forward_snapshot(dict(raw, items=[{"text": "输入框", "automation_id": "chat_input_field"}]), config, "parent")


if __name__ == "__main__":
    unittest.main()
