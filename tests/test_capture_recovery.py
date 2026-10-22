from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.analyzer import analyze_message
from wechat_tracker.engine import Engine
from wechat_tracker.macos import _helper, normalize_snapshot
from wechat_tracker.notify import format_notification, format_notification_html
from wechat_tracker.store import Store
from wechat_tracker.stream import NativeStream
from tests.test_macos import CONFIG, snapshot


class CaptureRecoveryTests(unittest.TestCase):
    def test_file_notice_uses_filename_and_discloses_unread_body_in_both_parts(self):
        raw = snapshot()
        raw['items'] = [dict(text='文件\n讲座安排<测试>.pdf\n37.6K\n未下载\n微信电脑版', row_index=2)]
        message, = normalize_snapshot(raw, CONFIG)
        event, = analyze_message(message)
        self.assertEqual(event['title'], '讲座安排<测试>.pdf')
        self.assertIsNone(event['signup_start_at'])
        self.assertFalse(message['source_meta']['pending_expansion'])
        subject, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertIn('文件：讲座安排<测试>.pdf', subject)
        self.assertIn('文件消息；仅记录文件名，未下载、未读取文件正文', body)
        self.assertIn('未下载、未读取文件正文', html)
        self.assertIn('&lt;测试&gt;', html)
        self.assertNotIn('37.6K', body)
        self.assertNotIn('微信电脑版', body)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'tracker.sqlite3')
            try:
                engine = Engine(store, dict(CONFIG, reminder_minutes=[]))
                self.assertEqual(len(engine.capture_snapshot([message], include_existing=True)), 1)
                self.assertEqual(engine.capture_snapshot([message], include_existing=True), [])
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)
            finally:
                store.close()

    def test_unrelated_file_is_not_made_into_a_lecture(self):
        self.assertEqual(analyze_message(dict(id='file', kind='file', text='文件\n采购表.pdf\n30K')), [])

    def test_recovery_is_opt_in_and_routed_to_stream_and_capture_only(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / 'helper'
            helper.touch()
            config = deepcopy(CONFIG)
            config['capture'].update(mac_helper=str(helper), recover_window=True, follow_tail=True)
            with patch('wechat_tracker.stream.sys.platform', 'darwin'):
                command = NativeStream(config).command()
            self.assertIn('--recover-window', command)
            self.assertIn('--follow-tail', command)
            with patch('wechat_tracker.macos.sys.platform', 'darwin'), patch('wechat_tracker.macos.subprocess.run') as run:
                run.return_value.stdout = ('{"ok":true,"group":"' + CONFIG['group'] + '"}').encode()
                _helper(config, 'capture')
                self.assertIn('--recover-window', run.call_args.args[0])
                _helper(config, 'probe')
                self.assertNotIn('--recover-window', run.call_args.args[0])
                self.assertNotIn('--follow-tail', run.call_args.args[0])
            config['capture'].pop('recover_window')
            with patch('wechat_tracker.stream.sys.platform', 'darwin'):
                self.assertNotIn('--recover-window', NativeStream(config).command())


if __name__ == '__main__':
    unittest.main()
