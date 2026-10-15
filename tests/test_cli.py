from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.cli import main
from wechat_tracker.store import Store


class WatchCliTests(unittest.TestCase):
    def test_tick_send_requires_local_or_environment_credentials_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('group = "2026级计算机研究生通知群"\ndata_dir = "state"\n', encoding="utf-8")
            with patch("wechat_tracker.credentials.apply_credentials", return_value=False) as credentials, \
                 patch("wechat_tracker.engine.send_email", side_effect=AssertionError("no SMTP calls")), \
                 redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(["--config", str(config), "tick", "--send"]), 2)
            credentials.assert_called_once()

    def test_jsonl_watch_once_imports_nested_lecture_and_repeat_does_not_duplicate(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config.toml"
            config.write_text('group = "2026级计算机研究生通知群"\n'
                              'data_dir = "state"\n[capture]\nsource = "jsonl"\n'
                              f'jsonl_path = {json.dumps(str(root / "examples" / "forward.jsonl"))}\n', encoding="utf-8")
            db = base / "state" / "tracker.sqlite3"
            args = ["--config", str(config), "watch", "--once", "--include-existing", "--expand-forward"]
            # A JSONL source must never invoke native card input, even if enabled.
            with patch("wechat_tracker.capture._run_uia", side_effect=AssertionError("no UI calls")), \
                 patch("wechat_tracker.engine.send_email", side_effect=AssertionError("no SMTP calls")), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
            self.assertIn('"capture_ok": true', output.getvalue())
            store = Store(db)
            try:
                events = store.events()
                self.assertEqual(len(events), 2)
                scheduled = [event for event in events if event.get("schedule_ready")]
                self.assertEqual(len(scheduled), 1)
                self.assertEqual(scheduled[0]["start_at"], "2026-10-20T14:00:00+08:00")
                self.assertEqual(sum(event.get("record_type") == "review" for event in events), 1)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
                self.assertTrue(all(job["status"] == "pending" for job in store.jobs()))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
