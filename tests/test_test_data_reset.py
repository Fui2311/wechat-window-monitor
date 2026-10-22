"""Reset acceptance: real control socket, preserved baseline, and HTTP isolation."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from console_fixtures import isolated_console

from wechat_tracker.capture import CaptureError
from wechat_tracker.engine import Engine
from wechat_tracker.service import MonitorService, read_status, reset_test_data
from wechat_tracker.store import Store
from wechat_tracker.web import WebControlHandler, WebControlServer, _list_services


class IdleStream:
    pid = 12345
    def start(self):
        pass
    def close(self):
        pass
    def next_frame(self, timeout=0.5):
        time.sleep(0.01)
        return None


class TestDataResetTests(unittest.TestCase):
    def settings(self, directory, group="邮件测试"):
        return dict(group=group, data_dir=str(directory), reminder_minutes=[], email={},
                    capture=dict(source="windows_uia", auto_expand_forward=True),
                    service=dict(backup_enabled=False, include_existing=False, prevent_sleep=False,
                                 dispatch_seconds=60))

    def message(self, identifier="old"):
        return dict(id=identifier, group="邮件测试", kind="text", source="windows_uia",
                    text="讲座通知：测试原文\n活动时间：2026年10月22日15:00")

    def test_control_reset_preserves_sending_and_accepts_new_copy_without_replaying_baseline(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            cfg = self.settings(directory)
            old = self.message()
            forward = dict(self.message("old-card"), kind="forward", text="群聊的聊天记录 讲座通知")
            store = Store(Path(directory) / "tracker.sqlite3")
            Engine(store, cfg).capture_snapshot([old], include_existing=True)
            store.close()
            service = MonitorService(cfg, send=True, stream_factory=lambda _: IdleStream())
            with patch("wechat_tracker.credentials.apply_credentials", return_value=False), \
                    patch("wechat_tracker.service.capture_once", return_value=[old, forward]) as capture, \
                    patch("wechat_tracker.engine.send_email", side_effect=AssertionError("no SMTP on reset")):
                thread = threading.Thread(target=service.run, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline and not (Path(directory) / "service.json").exists():
                        time.sleep(0.01)
                    status = read_status(cfg)
                    with self.assertRaisesRegex(ValueError, "服务已发生变化"):
                        reset_test_data(cfg, instance="stale-instance")
                    result = reset_test_data(cfg, instance=status["instance"])
                    self.assertTrue(result["send_enabled"])
                    self.assertEqual(result["cleared"]["messages"], 1)
                    self.assertEqual(result["baseline_count"], 2)
                    self.assertFalse(capture.call_args.args[0]["capture"]["follow_tail"])
                    self.assertFalse(capture.call_args.args[0]["capture"]["recover_window"])
                    self.assertEqual(read_status(cfg)["instance"], status["instance"])
                    self.assertTrue(service.send)
                finally:
                    service.stop.set()
                    thread.join(3)
                self.assertFalse(thread.is_alive())
            store = Store(Path(directory) / "tracker.sqlite3")
            try:
                self.assertEqual(store.events(), [])
                self.assertEqual(store.jobs(), [])
                self.assertEqual(store.db.execute("SELECT status FROM forward_reads").fetchone()[0], "baseline")
                engine = Engine(store, cfg)
                self.assertEqual(engine.capture_snapshot([old, forward]), [])
                engine.capture_snapshot([self.message("new-copy")])
                self.assertEqual(len(store.events()), 1)
                self.assertEqual(len(store.jobs()), 1)
                self.assertEqual(store.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(list(Path(directory).glob("*last-good*")), [])
            finally:
                store.close()

    def test_capture_failure_and_formal_group_leave_records_unchanged(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            service = MonitorService(self.settings(directory))
            service.store = Store(":memory:")
            try:
                Engine(service.store, service.config).capture_snapshot([self.message()], include_existing=True)
                with patch("wechat_tracker.service.capture_once", side_effect=CaptureError("window not ready")):
                    with self.assertRaises(CaptureError):
                        service._reset_test_data()
                self.assertEqual(len(service.store.events()), 1)
                self.assertEqual(len(service.store.jobs()), 1)
                service.config["group"] = "正式群"
                with self.assertRaises(ValueError):
                    service._reset_test_data()
                self.assertEqual(len(service.store.events()), 1)
            finally:
                service.store.close()
                for handler in list(service.log.handlers):
                    handler.close()
                    service.log.removeHandler(handler)

    def test_http_reset_requires_identified_test_group_confirmation_and_same_origin(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            cfg_path = Path(directory) / "config.toml"
            cfg_path.write_text('group = "正式群"\ndata_dir = "data"\n')
            server = WebControlServer(("127.0.0.1", 0), WebControlHandler, cfg_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            test_dir = str((Path(directory) / "test").resolve())
            records = [dict(group="邮件测试", data_dir=test_dir, running=True, instance="live")]
            def request(body, origin=base):
                req = urllib.request.Request(base + "/api/services/reset", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json", "Origin": origin})
                try:
                    response = urllib.request.urlopen(req)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.loads(response.read())
            try:
                with patch("wechat_tracker.console.handler._list_services", return_value=records), \
                        patch("wechat_tracker.console.handler.reset_test_data", return_value=dict(ok=True, baseline_count=2)) as reset:
                    payload = dict(data_dir=test_dir, instance="live", confirm_group="邮件测试")
                    self.assertEqual(request(payload, "https://unrelated.example")[0], 403)
                    self.assertEqual(request(dict(payload, confirm_group="正式群"))[0], 400)
                    self.assertEqual(request(dict(payload, data_dir=server.config["data_dir"]))[0], 403)
                    reset.assert_not_called()
                    self.assertEqual(request(payload)[0], 200)
                    reset.assert_called_once_with({"data_dir": test_dir}, instance="live")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_nested_test_service_is_discovered_and_tokens_are_not_exposed(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            root = Path(directory).resolve() / "state"
            current = root / "formal" / "data"
            test = root / "test" / "data"
            current.mkdir(parents=True)
            test.mkdir(parents=True)
            (test / "service.json").write_text(json.dumps(dict(group="邮件测试", running=False,
                control_token="private", reset_supported=True)))
            services = _list_services(self.settings(current, "正式群"), "config.toml")
            found = next(svc for svc in services if svc["data_dir"] == str(test))
            self.assertEqual(found["group"], "邮件测试")
            self.assertNotIn("control_token", found)


if __name__ == "__main__":
    unittest.main()
