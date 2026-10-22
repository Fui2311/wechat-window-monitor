from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.capture import CaptureError, read_forward
from wechat_tracker.engine import Engine, parse_time
from wechat_tracker.macos import _forward_request, expand_forward, normalize_snapshot, read_open_forward
from wechat_tracker.store import Store
from wechat_tracker.watcher import enrich_forwards

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
        self.assertEqual(row["source_meta"]["group_binding"], dict(method="exact_window_title"))

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

    def test_identical_new_card_keeps_its_identity_when_old_copy_leaves_viewport(self):
        raw = snapshot()
        text = '聊天记录群聊的聊天记录讲座报名通知'
        raw['items'] = [dict(text=text, row_index=20, content_ordinal=0),
                        dict(text=text, row_index=23, content_ordinal=1)]
        old, new = normalize_snapshot(raw, CONFIG)
        clipped = dict(raw, items=[dict(raw['items'][1], row_index=5)])
        self.assertEqual(normalize_snapshot(clipped, CONFIG)[0]['id'], new['id'])
        self.assertNotEqual(old['id'], new['id'])
        store = Store(':memory:')
        try:
            engine = Engine(store, dict(CONFIG, reminder_minutes=[]))
            engine.capture_snapshot([old], include_existing=False)
            imported = engine.capture_snapshot(normalize_snapshot(clipped, CONFIG))
            self.assertEqual(len(imported), 1)
            self.assertEqual(len(store.jobs()), 1)
            self.assertEqual(engine.capture_snapshot([new]), [])
        finally:
            store.close()

    def test_loaded_occurrence_rejects_negative_boolean_or_unbounded_values(self):
        for ordinal in (-1, True, 200, '1'):
            raw = snapshot()
            raw['items'][0]['content_ordinal'] = ordinal
            with self.subTest(ordinal=ordinal), self.assertRaises(CaptureError):
                normalize_snapshot(raw, CONFIG)

    def test_ax_rows_distinguish_equal_cards_without_reading_offscreen_old_text(self):
        config = deepcopy(CONFIG)
        config['capture']['mac_message_identity'] = 'ax_row'
        raw = snapshot()
        text = '聊天记录群聊的聊天记录讲座报名通知'
        raw['items'] = [dict(text=text, row_index=20, row_token='100', content_ordinal=0),
                        dict(text=text, row_index=23, row_token='101', content_ordinal=1)]
        old, new = normalize_snapshot(raw, config)
        clipped = dict(raw, items=[dict(raw['items'][1], row_index=5, content_ordinal=0)])
        self.assertEqual(normalize_snapshot(clipped, config)[0]['id'], new['id'])
        self.assertNotEqual(old['id'], new['id'])
        self.assertNotEqual(normalize_snapshot(dict(clipped, process_id=43), config)[0]['id'], new['id'])
        store = Store(':memory:')
        try:
            engine = Engine(store, dict(config, reminder_minutes=[]))
            engine.capture_snapshot([old], include_existing=False)
            self.assertEqual(len(engine.capture_snapshot([new])), 1)
            self.assertEqual(engine.capture_snapshot(normalize_snapshot(clipped, config)), [])
            self.assertEqual(len(store.jobs()), 1)
        finally:
            store.close()
        clipped['items'][0].pop('row_token')
        with self.assertRaises(CaptureError):
            normalize_snapshot(clipped, config)

    def test_attachments_and_forward_cards_stay_explicit(self):
        raw = snapshot()
        raw["items"] = [dict(text="文件 名单.pdf", row_index=0), dict(text="聊天记录群聊的聊天记录通知", row_index=1)]
        rows = normalize_snapshot(raw, CONFIG)
        self.assertEqual([row["kind"] for row in rows], ["file", "forward"])
        self.assertFalse(rows[0]["source_meta"]["pending_expansion"])
        self.assertEqual(rows[0]["source_meta"]["file_content_policy"], "metadata_only")
        self.assertTrue(rows[1]["source_meta"]["pending_expansion"])

    def test_unsafe_timestamp_and_oversized_batch_fail_closed(self):
        raw = snapshot()
        raw["observed_at"] = "2026-10-15T14:00:00"
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, CONFIG)
        raw = snapshot()
        raw["items"] *= 200
        with self.assertRaises(CaptureError):
            normalize_snapshot(raw, CONFIG)


class ManualMacForwardTests(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(CONFIG)
        self.config['capture']['forward_window_title'] = '群聊的聊天记录'
        raw = snapshot()
        raw['items'] = [dict(text='聊天记录\n【活动报名】测试讲座', row_index=7, fully_visible=True)]
        self.parent, = normalize_snapshot(raw, self.config)
        request = _forward_request(self.config, self.parent)
        self.result = dict(snapshot(), window_title='群聊的聊天记录', items=[
            dict(text='活动开始：2026年10月17日14:30\n2026年10月15日 15:20', fully_visible=True),
            dict(text='报名截止：明天', fully_visible=True)],
            binding=dict(method='explicit_manual_parent_id', read_only=True, ui_actions_performed=False,
                         detail_window_count=1, process_id=42, parent_row_index=7,
                         parent_text_sha256=request['text_sha256']))

    def test_manual_read_retains_uncertainty_and_original_timestamp(self):
        with patch('wechat_tracker.macos._helper', return_value=self.result) as helper:
            result = read_open_forward(self.config, self.parent)
        self.assertEqual(helper.call_args.args[1], 'read-forward')
        self.assertFalse(result['source_group_verified'])
        self.assertTrue(result['incomplete'])
        self.assertTrue(result['source_meta']['detail_read_only'])
        self.assertEqual(result['source_meta']['identity_binding'], 'explicit_manual_parent_id')
        first, second = result['children']
        self.assertEqual(first['sent_at'], '2026-10-15T15:20:00+08:00')
        self.assertEqual(first['text'], '活动开始：2026年10月17日14:30')
        self.assertIsNone(second['sent_at'])
        self.assertTrue(all(row['group'] == GROUP and not row['source_meta']['source_group_verified']
                            for row in result['children']))

    def test_changed_parent_ambiguous_detail_or_ui_action_is_rejected(self):
        changes = [dict(method='verified_parent_open'), dict(read_only=False),
                   dict(ui_actions_performed=True), dict(detail_window_count=2),
                   dict(process_id=43), dict(parent_row_index=8), dict(parent_text_sha256='changed')]
        for change in changes:
            result = deepcopy(self.result)
            result['binding'].update(change)
            with self.subTest(change=change), patch('wechat_tracker.macos._helper', return_value=result):
                with self.assertRaises(CaptureError):
                    read_open_forward(self.config, self.parent)
        for change in [dict(ok=False, status='detail_not_open'), dict(process_id=43),
                       dict(window_title='其他人的聊天记录'), dict(list_count=2), dict(bundle_id='other.app')]:
            with self.subTest(change=change), patch('wechat_tracker.macos._helper', return_value=dict(self.result, **change)):
                with self.assertRaises(CaptureError):
                    read_open_forward(self.config, self.parent)

    def test_wrong_group_parent_never_calls_native_helper(self):
        for change in [dict(group='其他群'), dict(source='windows_uia'), dict(kind='text')]:
            with self.subTest(change=change), patch('wechat_tracker.macos._helper') as helper:
                with self.assertRaises(CaptureError):
                    read_open_forward(self.config, dict(self.parent, **change))
                helper.assert_not_called()

    def test_passive_read_accepts_clipping_but_expansion_rejects_it(self):
        parent = deepcopy(self.parent)
        parent['source_meta']['fully_visible'] = False
        with patch('wechat_tracker.macos._helper', return_value=self.result) as helper:
            result = read_open_forward(self.config, parent)
        self.assertEqual(helper.call_args.args[1], 'read-forward')
        self.assertFalse(result['source_group_verified'])
        with patch('wechat_tracker.macos._helper') as helper:
            with self.assertRaises(CaptureError):
                expand_forward(self.config, parent)
            helper.assert_not_called()

    def test_main_window_parent_requires_configured_exact_header_binding(self):
        self.config['capture']['mac_header_identifier'] = 'current_chat_name_label'
        raw = snapshot()
        raw.update(window_title='微信', group_binding=dict(method='explicit_header_identifier',
                   header_identifier='current_chat_name_label', header_value=GROUP),
                   items=[dict(text=self.parent['text'], row_index=7, fully_visible=True)])
        parent, = normalize_snapshot(raw, self.config)
        with patch('wechat_tracker.macos._helper', return_value=self.result):
            result = read_open_forward(self.config, parent)
        self.assertEqual(result['group'], GROUP)
        for binding in ({}, dict(raw['group_binding'], header_value='其他群'),
                        dict(raw['group_binding'], header_identifier='session_item_title'),
                        dict(raw['group_binding'], method='guessed_header')):
            changed = deepcopy(parent)
            changed['source_meta']['group_binding'] = binding
            with self.subTest(binding=binding), patch('wechat_tracker.macos._helper') as helper:
                with self.assertRaises(CaptureError):
                    read_open_forward(self.config, changed)
                helper.assert_not_called()
        del self.config['capture']['mac_header_identifier']
        with patch('wechat_tracker.macos._helper') as helper:
            with self.assertRaises(CaptureError):
                read_open_forward(self.config, parent)
            helper.assert_not_called()

    def test_cli_capture_route_requires_fresh_explicit_parent(self):
        self.config['capture']['forward_parent_id'] = self.parent['id']
        with patch('wechat_tracker.macos.capture_once', return_value=[self.parent]), \
                patch('wechat_tracker.macos.read_open_forward', return_value={'read': True}) as reader, \
                patch('wechat_tracker.capture._run_uia') as windows:
            self.assertEqual(read_forward(self.config, self.parent['id']), {'read': True})
            reader.assert_called_once_with(self.config, self.parent)
            windows.assert_not_called()
        with patch('wechat_tracker.macos.capture_once', return_value=[]), \
                patch('wechat_tracker.macos.read_open_forward') as reader:
            with self.assertRaisesRegex(CaptureError, 'no longer present'):
                read_forward(self.config, self.parent['id'])
            reader.assert_not_called()
        with patch('wechat_tracker.macos.capture_once') as capture:
            with self.assertRaisesRegex(CaptureError, 'explicitly bind'):
                read_forward(self.config, 'unselected-parent')
            capture.assert_not_called()


class ActivatedMacForwardTests(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(CONFIG)
        self.config['capture']['mac_forward_interaction'] = 'activate_click'
        raw = snapshot()
        raw['items'] = [dict(text='聊天记录群聊的聊天记录甲：讲座通知', row_index=6, fully_visible=False)]
        self.parent, = normalize_snapshot(raw, self.config)
        request = _forward_request(self.config, self.parent, require_fully_visible=False)
        self.result = dict(snapshot(), window_title='群聊的聊天记录', items=[
            dict(text='讲座主题：《真实正文》\n讲座时间：2026年10月18日14:30\n2026年10月15日 15:20')],
            diagnostics=dict(history_complete=False, interaction_seconds=0.8),
            binding=dict(process_id=42, parent_row_index=6, parent_text_sha256=request['text_sha256'],
                         new_window_observed=True, restored=True, interaction_method='activate_click',
                         activation_requested=True, global_input_used=True, cursor_restored=True,
                         notice_completed=True, notice_seconds=5,
                         foreground_restored=True, foreground_pid_before=100, foreground_pid_after=100))

    def test_activated_open_verifies_linkage_and_restoration_before_accepting_body(self):
        with patch('wechat_tracker.macos._helper', return_value=self.result) as helper:
            result = expand_forward(self.config, self.parent)
        self.assertEqual(helper.call_args.args[1], 'expand-activated')
        self.assertTrue(result['source_group_verified'])
        self.assertTrue(result['incomplete'])
        self.assertTrue(result['source_meta']['binding']['global_input_used'])
        self.assertEqual(result['source_meta']['forward_diagnostics']['interaction_seconds'], 0.8)
        self.assertEqual(result['children'][0]['sent_at'], '2026-10-15T15:20:00+08:00')

    def test_missing_focus_cursor_or_parent_proof_is_rejected(self):
        changes = [dict(foreground_restored=False), dict(foreground_pid_before=0),
                   dict(foreground_pid_before=True), dict(foreground_pid_after=101),
                   dict(cursor_restored=False), dict(activation_requested=False),
                   dict(global_input_used=False), dict(interaction_method='ax_press'),
                   dict(notice_completed=False), dict(notice_seconds=0), dict(notice_seconds=True),
                   dict(parent_text_sha256='stale'), dict(new_window_observed=False), dict(restored=False)]
        for change in changes:
            raw = deepcopy(self.result)
            raw['binding'].update(change)
            with self.subTest(change=change), patch('wechat_tracker.macos._helper', return_value=raw):
                with self.assertRaises(CaptureError):
                    expand_forward(self.config, self.parent)
        for change in [dict(process_id=43), dict(window_title='其他人的聊天记录'),
                       dict(list_count=2), dict(bundle_id='other.app'), dict(ok=False, status='user_busy')]:
            with self.subTest(change=change), patch('wechat_tracker.macos._helper', return_value=dict(self.result, **change)):
                with self.assertRaises(CaptureError):
                    expand_forward(self.config, self.parent)

    def test_activated_input_requires_independent_window_and_explicit_mode(self):
        config = deepcopy(self.config)
        config['capture']['mac_header_identifier'] = 'current_chat_name_label'
        parent = deepcopy(self.parent)
        parent['source_meta'].update(window_title='微信', group_binding=dict(method='explicit_header_identifier',
                                    header_identifier='current_chat_name_label', header_value=GROUP))
        with patch('wechat_tracker.macos._helper') as helper:
            with self.assertRaises(CaptureError):
                expand_forward(config, parent)
            helper.assert_not_called()
        del config['capture']['mac_forward_interaction']
        with patch('wechat_tracker.macos._helper') as helper:
            with self.assertRaises(CaptureError):
                expand_forward(config, self.parent)
            helper.assert_not_called()

    def test_watcher_caches_verified_body_and_engine_deduplicates_it(self):
        config = deepcopy(self.config)
        config.update(reminder_minutes=[])
        config['capture']['auto_expand_forward'] = True
        now = parse_time('2026-10-16T12:00:00+08:00')
        store = Store(':memory:')
        try:
            engine = Engine(store, config)
            engine.capture_snapshot([], now=now)
            with patch('wechat_tracker.macos._helper', return_value=self.result) as helper:
                messages, diagnostics = enrich_forwards([self.parent], store, config, now=now)
                first = engine.capture_snapshot(messages, now=now)
                repeated, _ = enrich_forwards([self.parent], store, config, now=now)
                second = engine.capture_snapshot(repeated, now=now)
                later, _ = enrich_forwards([self.parent], store, config, now=now + timedelta(days=1))
                self.assertEqual(later, repeated)
            helper.assert_called_once()
            self.assertEqual(diagnostics[0]['status'], 'partial')
            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(len(store.events()), 1)
            self.assertEqual(len(store.jobs()), 1)
            self.assertEqual(store.jobs()[0]['reason'], 'new')
            self.assertTrue(all(job['status'] == 'pending' for job in store.jobs()))
            self.assertEqual(store.db.execute('SELECT status FROM forward_reads').fetchone()[0], 'partial')
            self.assertIsNone(store.db.execute('SELECT next_attempt_at FROM forward_reads').fetchone()[0])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
