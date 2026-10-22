from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.cli import main
from wechat_tracker.store import Store


class WatchCliTests(unittest.TestCase):
    def test_init_creates_loadable_platform_config_and_refuses_overwrite(self):
        from wechat_tracker.config import load_config
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.local.toml"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(target), "--platform", "windows", "--group", "精确测试群"]), 0)
            config = load_config(target)
            self.assertEqual(config["group"], "精确测试群")
            self.assertEqual(config["capture"]["source"], "windows_uia")
            self.assertEqual(config["data_dir"], str((target.parent / "state").resolve()))
            original = target.read_bytes()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["init", str(target)]), 2)
            self.assertEqual(target.read_bytes(), original)

    def test_demo_ignores_live_group_and_llm_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.local.toml"
            target.write_text('group = "另一群"\ndata_dir = "state"\nprocessing_mode = "llm"\nllm_model = "forbidden-model"\n', encoding="utf-8")
            with patch("urllib.request.build_opener", side_effect=AssertionError("no model access")), \
                    patch("wechat_tracker.engine.send_email", side_effect=AssertionError("no SMTP")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--config", str(target), "demo"]), 0)
                self.assertEqual(main(["--config", str(target), "demo"]), 0)
            store = Store(target.parent / "state/demo.sqlite3")
            try:
                self.assertEqual(len(store.events()), 1)
                self.assertEqual(len(store.jobs()), 1)
                self.assertEqual(store.events()[0]["processing_method"], "rules")
                self.assertEqual(store.jobs()[0]["status"], "pending")
            finally:
                store.close()

    def test_packaged_configuration_templates_match_editable_examples(self):
        from wechat_tracker.paths import PROJECT_ROOT, RESOURCE_ROOT
        self.assertEqual((RESOURCE_ROOT / "config.windows.toml").read_bytes(), (PROJECT_ROOT / "config.example.toml").read_bytes())
        self.assertEqual((RESOURCE_ROOT / "config.macos.toml").read_bytes(), (PROJECT_ROOT / "mac/config.example.toml").read_bytes())

    def test_reanalysis_refuses_live_database_even_with_db_override(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text('group = "测试群"\ndata_dir = "state"\n', encoding="utf-8")
            with patch("wechat_tracker.service.read_status", return_value={"running": True}), \
                 patch("wechat_tracker.engine.Engine.rebuild_analysis") as rebuild, \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(config), "--db", str(Path(directory) / "state/tracker.sqlite3"),
                                       "rebuild-analysis"]), 2)
            rebuild.assert_not_called()

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
                self.assertEqual(len(events), 1)
                scheduled = [event for event in events if event.get("schedule_ready")]
                self.assertEqual(len(scheduled), 1)
                self.assertEqual(scheduled[0]["start_at"], "2026-10-20T14:00:00+08:00")
                self.assertTrue(all(event.get("record_type") == "notice" for event in events))
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
                self.assertTrue(all(job["status"] == "pending" for job in store.jobs()))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
