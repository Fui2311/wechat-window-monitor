from pathlib import Path
from contextlib import closing
import runpy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'scripts/inspect-macos.py'))
inspect = MODULE['inspect']
GLOBAL = inspect.__globals__


class MacInspectionTests(unittest.TestCase):
    def test_default_view_omits_message_text_and_control_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(group='target', data_dir=directory, capture=dict(source='macos_ax'))
            messages = [dict(id='one', kind='forward', text='private source text', sent_at=None,
                             source_meta=dict(pending_expansion=True))]
            with patch.dict(GLOBAL, read_status=lambda _: dict(running=True, status='monitoring', control_token='private-control-token'),
                            probe=lambda _: dict(ok=True, group='target', unexpected_secret='private-probe-secret'),
                            capture_once=lambda _: messages):
                result = inspect(config)
            output = str(result)
            for secret in ('private source text', 'private-control-token', 'private-probe-secret'):
                self.assertNotIn(secret, output)
            self.assertNotIn('messages', result)
            self.assertEqual(result['capture']['pending_expansion'], 1)
            self.assertEqual(result['capture']['original_time_unknown'], 1)

    def test_unready_group_does_not_attempt_capture_and_database_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'tracker.sqlite3'
            with closing(sqlite3.connect(database)) as db:
                for name in ('messages', 'events', 'outbox', 'capture_seen'):
                    db.execute('CREATE TABLE ' + name + '(id INTEGER)')
            before = database.read_bytes()
            config = dict(group='target', data_dir=directory, capture=dict(source='macos_ax'))
            with patch.dict(GLOBAL, read_status=lambda _: dict(running=False),
                            probe=lambda _: dict(ok=False, status='target_window_not_ready'),
                            capture_once=lambda _: self.fail('must not read another window')):
                result = inspect(config)
            self.assertNotIn('capture', result)
            self.assertEqual(result['database']['integrity'], 'ok')
            self.assertTrue(result['database']['read_only'])
            self.assertEqual(database.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
