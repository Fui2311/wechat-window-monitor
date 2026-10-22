"""Read-only queue disclosure, including retries and timezone-aware due dates."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from wechat_tracker.console.queries import delivery_backlog
from wechat_tracker.store import Store


class ConsoleBacklogTests(unittest.TestCase):
    def test_disclosure_counts_pending_and_dead_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'tracker.sqlite3'
            now = datetime(2026, 10, 22, 8, tzinfo=timezone.utc).isoformat()
            empty = delivery_backlog(path, now)
            self.assertEqual(empty['pending'], 0)
            self.assertFalse(path.exists())
            store = Store(path)
            for index, (status, due, attempts) in enumerate([
                ('pending', '2026-10-21T00:00:00Z', 0),
                ('pending', '2026-10-22T16:00:00+08:00', 1),
                ('pending', '2026-10-23T00:00:00Z', 0),
                ('dead', '2026-10-20T00:00:00Z', 5),
                ('sent', '2026-10-19T00:00:00Z', 0),
            ]):
                store.db.execute('INSERT INTO outbox(dedup_key,event_id,version,reason,due_at,payload,status,attempts) VALUES (?,?,1,?,?,?,?,?)',
                                 (str(index),str(index),'new',due,'{}',status,attempts))
            store.db.commit()
            before = list(store.db.execute('SELECT * FROM outbox'))
            result = delivery_backlog(path, now)
            self.assertEqual(result, dict(pending=3, due=2, retrying=1, failed=1,
                                          oldest_due_at='2026-10-21T00:00:00Z'))
            self.assertEqual(list(store.db.execute('SELECT * FROM outbox')), before)
            store.close()
