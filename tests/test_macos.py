from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from wechat_tracker.capture import CaptureError
from wechat_tracker.macos import normalize_snapshot

GROUP = "2026级计算机研究生通知群"
CONFIG = dict(group=GROUP, capture=dict(source="macos_ax", bundle_id="com.tencent.xinWeChat"))


def snapshot():
    return dict(ok=True, status="ready", source="macos_ax", group=GROUP, bundle_id="com.tencent.xinWeChat",
                process_id=42, window_title=GROUP, group_binding=dict(method="exact_window_title"),
                list_count=1, list_role="AXList", observed_at="2026-10-15T07:00:00Z",
                items=[dict(text="讲座时间：2026年10月17日14:30，地点：101", row_index=3, fully_visible=True)])


class MacCaptureContractTests(unittest.TestCase):
    def test_macos_message_preserves_unknown_original_sender_and_time(self):
        row, = normalize_snapshot(snapshot(), CONFIG)
        self.assertEqual(row["source"], "macos_ax")
        self.assertIsNone(row["sender"])
        self.assertIsNone(row["sent_at"])
        self.assertEqual(row["source_meta"]["ax_row_index"], 3)

    def test_generic_window_does_not_prove_the_selected_group(self):
        raw = snapshot()
        raw["window_title"] = "微信"
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, CONFIG)

    def test_explicit_exact_header_identifier_can_bind_a_main_window(self):
        config = deepcopy(CONFIG)
        config["capture"]["mac_header_identifier"] = "verified-current-chat-title"
        raw = snapshot()
        raw.update(window_title="微信", group_binding=dict(method="explicit_header_identifier",
                   header_identifier="verified-current-chat-title", header_value=GROUP))
        self.assertEqual(normalize_snapshot(raw, config)[0]["group"], GROUP)
        raw["group_binding"]["header_identifier"] = "contacts-row-title"
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, config)

    def test_foreign_app_or_ambiguous_lists_are_rejected(self):
        for change in (dict(bundle_id="other.app"), dict(group="其他群"), dict(list_count=2),
                       dict(list_role="AXTextField"), dict(process_id=0)):
            with self.subTest(change=change), self.assertRaises(CaptureError):
                normalize_snapshot(dict(snapshot(), **change), CONFIG)

    def test_identity_survives_observation_time_and_ax_row_movement(self):
        one = snapshot()
        two = deepcopy(one)
        two["observed_at"] = "2026-10-15T07:01:00Z"
        two["items"][0]["row_index"] = 4
        self.assertEqual(normalize_snapshot(one, CONFIG)[0]["id"], normalize_snapshot(two, CONFIG)[0]["id"])

    def test_attachments_and_forward_cards_stay_explicit(self):
        raw = snapshot()
        raw["items"] = [dict(text="文件 名单.pdf", row_index=0), dict(text="聊天记录群聊的聊天记录通知", row_index=1)]
        rows = normalize_snapshot(raw, CONFIG)
        self.assertEqual([row["kind"] for row in rows], ["file", "forward"])
        self.assertTrue(all(row["source_meta"]["pending_expansion"] for row in rows))

    def test_unsafe_timestamp_and_oversized_batch_fail_closed(self):
        raw = snapshot()
        raw["observed_at"] = "2026-10-15T14:00:00"
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, CONFIG)
        raw = snapshot()
        raw["items"] *= 200
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, CONFIG)


if __name__ == "__main__":
    unittest.main()
