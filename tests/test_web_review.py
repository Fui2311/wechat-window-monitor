"""Independent HTTP checks for control isolation and failed group changes."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import urllib.parse

from wechat_tracker.web import WebControlHandler, WebControlServer
from wechat_tracker.cli import main


class WebControlReviewTests(unittest.TestCase):
    def setUp(self):
        startup = patch.object(WebControlHandler, "_pause_service_startup", return_value={"installed": False})
        startup.start()
        self.addCleanup(startup.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config_path = Path(self.temporary.name) / "config.toml"
        self.config_path.write_text('group = "alpha"\ndata_dir = "state"\nreminder_minutes = []\n')
        self.server = WebControlServer(("127.0.0.1", 0), WebControlHandler, self.config_path)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, *, origin=None, body=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_cross_site_cannot_read_status_or_receive_reflected_cors(self):
        status, headers, _ = self.request("/api/status", origin="https://unrelated.example")
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_web_demo_ignores_shared_processing_source_even_when_source_cannot_be_loaded(self):
        # CLI already isolates linked settings; the web replay must have the same contract.
        self.server.config['processing_config_path'] = 'nonexistent-shared.toml'
        with patch('wechat_tracker.extraction.pipeline.extract_llm', side_effect=AssertionError('model access forbidden')), \
                patch('wechat_tracker.engine.send_email', side_effect=AssertionError('SMTP forbidden')):
            status, _, body = self.request('/api/demo/run', body={})
        self.assertEqual(status, 200, body)
        database = json.loads(body)['db']
        with sqlite3.connect(database) as connection:
            event = json.loads(connection.execute('SELECT payload FROM events').fetchone()[0])
        self.assertEqual(event['processing_method'], 'rules')

    def test_other_loopback_port_is_a_different_origin(self):
        with patch("wechat_tracker.console.handler.start_service") as start:
            status, _, _ = self.request(
                "/api/service/start", origin="http://127.0.0.1:1", body={}
            )
        self.assertEqual(status, 403)
        start.assert_not_called()

    def test_console_stop_acknowledges_and_exits_without_changing_monitor(self):
        before = self.config_path.read_bytes()
        with patch("wechat_tracker.console.handler.stop_service") as stop, \
                patch("wechat_tracker.console.handler.start_service") as start, \
                patch.object(WebControlHandler, "_pause_service_startup") as pause:
            status, _, body = self.request(
                "/api/console/stop", origin=self.base_url, body={}
            )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {
                "ok": True, "console": "stopping", "monitor_unchanged": True,
            })
            self.thread.join(timeout=2)
            self.assertFalse(self.thread.is_alive())
            self.assertTrue(self.server.shutdown_requested.is_set())
            stop.assert_not_called()
            start.assert_not_called()
            pause.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_console_stop_rejects_cross_site_requests_and_get(self):
        status, _, _ = self.request("/api/console/stop", origin="https://unrelated.example", body={})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/console/stop")
        self.assertEqual(status, 404)
        self.assertFalse(self.server.shutdown_requested.is_set())
        self.assertTrue(self.thread.is_alive())
        status, _, _ = self.request("/api/status")
        self.assertEqual(status, 200)

    def test_failed_monitor_stop_keeps_old_group_and_config(self):
        before = self.config_path.read_bytes()
        with patch("wechat_tracker.console.handler.probe", return_value={"ok": True, "group": "beta"}), \
                patch("wechat_tracker.console.handler.stop_service", side_effect=RuntimeError("shutdown deadline")):
            status, _, _ = self.request("/api/group/switch", body={"group": "beta"})
        self.assertGreaterEqual(status, 400)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.server.config["group"], "alpha")
        self.assertEqual(tomllib.loads(self.config_path.read_text())["group"], "alpha")

    def test_new_group_has_an_active_database_option_before_first_capture(self):
        status, _, body = self.request("/api/databases")
        self.assertEqual(status, 200)
        active = [entry for entry in json.loads(body)["databases"] if entry["is_active"]]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["group_name"], "alpha")
        self.assertEqual(active[0]["event_count"], 0)
        database = active[0]["path"]
        for endpoint in ("events", "outbox", "messages"):
            status, _, body = self.request("/api/" + endpoint + "?" + urllib.parse.urlencode({"db": database}))
            self.assertEqual(status, 200, body)
            self.assertTrue(json.loads(body)["ok"])
        self.assertFalse(Path(database).exists())

    def test_failed_switch_does_not_start_a_previously_stopped_mac_monitor(self):
        before = self.config_path.read_bytes()
        with patch("wechat_tracker.console.handler.sys.platform", "darwin"), \
                patch("wechat_tracker.console.handler.probe", return_value={"ok": True}), \
                patch("wechat_tracker.console.handler.read_status", return_value={"running": False, "send_enabled": False}), \
                patch.object(WebControlHandler, "_pause_service_startup", return_value={"installed": True}), \
                patch("wechat_tracker.console.handler.stop_service"), \
                patch("wechat_tracker.console.handler.update_toml_root_keys", side_effect=RuntimeError("config write failed")), \
                patch("wechat_tracker.autostart.manage_startup") as startup:
            status, _, body = self.request("/api/group/switch", body={"group": "beta"})
        self.assertEqual(status, 500)
        self.assertIn("config write failed", json.loads(body)["error"])
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual([call.args[2] for call in startup.call_args_list], ["configure"])

    def test_demo_generates_events_independently_of_current_group_name(self):
        status, _, _ = self.request("/api/demo/run", body={})
        self.assertEqual(status, 200)
        demo_db = Path(self.server.config["data_dir"]) / "demo.sqlite3"
        with closing(sqlite3.connect(demo_db)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertGreater(count, 0)


class WebLauncherConfigTests(unittest.TestCase):
    def test_explicit_config_before_web_command_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chosen.toml"
            path.write_text('group = "chosen"\ndata_dir = "state"\n')
            with patch("wechat_tracker.web.run_web", return_value=0) as run:
                self.assertEqual(main(["--config", str(path), "web"]), 0)
            self.assertEqual(Path(run.call_args.args[0]), path)


if __name__ == "__main__":
    unittest.main()
