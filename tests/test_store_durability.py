import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from wechat_tracker.store import Store


class DurabilityTests(unittest.TestCase):
    def test_forced_exit_rolls_back_uncommitted_write_and_keeps_verified_backup(self):
        root = Path(__file__).resolve().parent.parent
        script = """
from wechat_tracker.store import Store
import json,sys,time
s=Store(sys.argv[1])
with s.db:
 s.db.execute("INSERT INTO capture_state VALUES ('committed','yes')")
s.db.execute("INSERT INTO capture_state VALUES ('uncommitted','no')")
print('transaction-open',flush=True)
time.sleep(60)
"""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.sqlite3"
            child = subprocess.Popen([sys.executable, "-c", script, str(database)], cwd=root, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "transaction-open")
                child.kill()
                child.wait(timeout=5)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
            store = Store(database)
            try:
                self.assertEqual(store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(store.db.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                self.assertEqual(store.db.execute("SELECT group_name FROM capture_state").fetchone()[0], "committed")
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM capture_state").fetchone()[0], 1)
                backup = Path(directory) / "last-good.sqlite3"
                store.backup(backup)
                store.backup(backup)
                self.assertTrue(backup.with_suffix(".previous.sqlite3").exists())
                recovered = Store(backup)
                try:
                    self.assertEqual(recovered.db.execute("SELECT group_name FROM capture_state").fetchone()[0], "committed")
                finally:
                    recovered.close()
            finally:
                store.close()

    def test_corrupt_database_is_not_silently_reinitialized(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "tracker.sqlite3"
            original = b"broken database evidence" * 100
            database.write_bytes(original)
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                Store(database)
            self.assertEqual(database.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
