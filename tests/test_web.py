import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from wechat_tracker.web import WebControlServer, WebControlHandler
from wechat_tracker.config import (
    find_default_config_path,
    resolve_config_path,
    resolve_group_data_dir,
    update_toml_root_keys,
)
from wechat_tracker.store import Store


class WebDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        startup = patch.object(WebControlHandler, "_pause_service_startup", return_value={"installed": False})
        startup.start()
        cls.addClassCleanup(startup.stop)
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.base_path = Path(cls.temp_dir.name)
        cls.config_path = cls.base_path / "config.toml"
        cls.data_dir = cls.base_path / "state" / "group-default"
        cls.data_dir.mkdir(parents=True, exist_ok=True)

        cls.config_path.write_text(
            f'# 系统主配置文件\n'
            f'group = "测试讲座通知群"\n'
            f'data_dir = {json.dumps(str(cls.data_dir))}\n'
            f'timezone = "Asia/Shanghai"\n'
            f'# 仅发现通知，报名由用户核对名单\n'
            f'reminder_minutes = []\n'
            f'max_attempts = 5\n\n'
            f'[service]\n'
            f'resync_seconds = 15\n'
            f'dispatch_seconds = 5\n',
            encoding="utf-8"
        )

        # Seed sample event and job in tracker.sqlite3
        cls.db_path = cls.data_dir / "tracker.sqlite3"
        store = Store(cls.db_path)
        sample_event = {
            "id": "test-event-001",
            "title": "量子计算前沿讲座",
            "summary": "量子计算前沿讲座学术报告。",
            "start_at": "2026-10-20T14:00:00+08:00",
            "end_at": None,
            "category": "lecture_notice",
            "signup_start_at": "2026-10-19T09:00:00+08:00",
            "signup_start_text": "2026年10月19日 09:00",
            "signup_url": "https://example.com/signup",
            "activity_time_text": "2026年10月20日 14:00",
            "needs_review": False,
            "schedule_ready": True,
            "status": "noticed",
            "version": 1,
            "updated_at": "2026-10-16T10:00:00+00:00",
            "source_text": "【活动通知】量子计算前沿讲座...",
        }
        store.db.execute(
            "INSERT INTO events (id, title_key, group_name, payload) VALUES (?, ?, ?, ?)",
            (sample_event["id"], "量子计算前沿讲座", "测试讲座通知群", json.dumps(sample_event, ensure_ascii=False))
        )
        sample_job = {
            "id": 1,
            "dedup_key": "test-event-001:1:new:",
            "event_id": "test-event-001",
            "version": 1,
            "reason": "new",
            "due_at": "2026-10-16T10:00:00+00:00",
            "payload": json.dumps(sample_event, ensure_ascii=False),
            "status": "pending",
            "attempts": 0,
        }
        store.db.execute(
            "INSERT INTO outbox (id, dedup_key, event_id, version, reason, due_at, payload, status, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sample_job["id"], sample_job["dedup_key"], sample_job["event_id"], sample_job["version"],
             sample_job["reason"], sample_job["due_at"], sample_job["payload"], sample_job["status"], sample_job["attempts"])
        )
        store.db.commit()
        store.close()

        # Start server on ephemeral port (port 0)
        cls.server = WebControlServer(("127.0.0.1", 0), WebControlHandler, cls.config_path)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp_dir.cleanup()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str, headers: dict = None):
        req_headers = {"Host": f"127.0.0.1:{self.port}"}
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(self._url(path), headers=req_headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def _post(self, path: str, body: dict = None, headers: dict = None):
        payload = json.dumps(body or {}).encode("utf-8")
        req_headers = {
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{self.port}",
            "Origin": f"http://127.0.0.1:{self.port}",
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(self._url(path), data=payload, headers=req_headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def test_client_module_assets_and_dependency_order(self):
        status, _, html = self._get("/")
        self.assertEqual(status, 200)
        for asset in ("presentation.js", "http.js"):
            with self.subTest(asset=asset):
                status, headers, body = self._get("/" + asset)
                self.assertEqual(status, 200)
                self.assertIn("javascript", headers.get("Content-Type"))
                self.assertIn(b"Object.freeze", body)
                self.assertLess(html.index(("/" + asset).encode()), html.index(b"/app.js"))
        status, _, _ = self._get("/unlisted-client-module.js")
        self.assertEqual(status, 404)

    def test_static_index_html(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type"))
        content = body.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("讲座监控控制台", content)

    def test_explicit_email_authorization_returns_only_status_and_sends_no_mail(self):
        with patch('wechat_tracker.credentials.authorize_credentials', return_value=dict(configured=True, smtp_sent=False)) as authorize, \
                patch('wechat_tracker.engine.send_email') as send:
            status, _, body = self._post('/api/email/authorize')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), dict(ok=True, configured=True, smtp_sent=False))
        authorize.assert_called_once()
        send.assert_not_called()

    def test_delivery_backlog_always_describes_managed_database_and_is_read_only(self):
        before = self.db_path.read_bytes()
        status, _, body = self._get('/api/service/backlog?db=/ignored/history.sqlite3')
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['group'], self.server.config['group'])
        self.assertEqual(result['db'], str((Path(self.server.config['data_dir'])/'tracker.sqlite3').resolve()))
        self.assertGreaterEqual(result['pending'], 1)
        self.assertIsNotNone(result['oldest_due_at'])
        self.assertEqual(before, self.db_path.read_bytes())

    def test_send_rejects_group_changed_after_confirmation_without_service_actions(self):
        with patch('wechat_tracker.console.handler.start_service') as start, \
                patch('wechat_tracker.console.handler.stop_service') as stop:
            status, _, body = self._post('/api/service/start_send', {'expected_group': '过时的群名'})
        self.assertEqual(status, 400)
        self.assertIn('管理群已变更', json.loads(body)['error'])
        start.assert_not_called()
        stop.assert_not_called()

    def test_demo_does_not_inherit_service_from_same_directory(self):
        handler = object.__new__(WebControlHandler)
        handler.server = self.server
        active = dict(data_dir=str(self.data_dir), running=True, send_enabled=True, send_ready=True)
        with patch('wechat_tracker.console.handler._list_services', return_value=[active]):
            demo = handler._delivery_service(self.data_dir/'demo.sqlite3')
            unrelated = handler._delivery_service(self.data_dir/'archive.sqlite3')
            live = handler._delivery_service(self.data_dir/'tracker.sqlite3')
        self.assertTrue(demo['simulation'])
        self.assertEqual(unrelated, {})
        self.assertTrue(live['running'])

    def test_static_app_css(self):
        status, headers, body = self._get("/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type"))
        content = body.decode("utf-8")
        self.assertIn("--bg-app", content)

    def test_static_app_js(self):
        status, headers, body = self._get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers.get("Content-Type"))
        content = body.decode("utf-8")
        self.assertIn("initApp", content)
        self.assertIn("signupLinkHtml", content)
        self.assertNotIn("置信度", content)
        self.assertNotIn("待核实线索", content)

    def test_favicon_no_content(self):
        status, _, _ = self._get("/favicon.ico")
        self.assertEqual(status, 204)

    def test_head_request(self):
        req = urllib.request.Request(self._url("/"), method="HEAD", headers={"Host": f"127.0.0.1:{self.port}"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type"))

    def test_options_cors_rejects_foreign_origin(self):
        req = urllib.request.Request(
            self._url("/api/status"),
            method="OPTIONS",
            headers={"Host": f"127.0.0.1:{self.port}", "Origin": "http://evil.com"}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 204)
        except urllib.error.HTTPError as err:
            self.assertEqual(err.code, 403)

    def test_security_cross_site_post_rejected(self):
        # Origin is evil.com
        status, _, body = self._post("/api/service/start", headers={"Origin": "http://evil.com"})
        self.assertEqual(status, 403)
        self.assertIn("Cross-site request rejected", body.decode("utf-8"))

        # Referer is malicious
        status, _, body = self._post(
            "/api/service/start",
            headers={"Origin": "", "Referer": "http://malicious.org/attack"}
        )
        self.assertEqual(status, 403)

    def test_security_untrusted_host_rejected(self):
        status, _, _ = self._get("/api/status", headers={"Host": "attacker.com"})
        self.assertEqual(status, 400)

    def test_security_arbitrary_database_path_rejected(self):
        status, _, body = self._get("/api/events?db=/etc/passwd.sqlite3")
        self.assertEqual(status, 403)
        self.assertIn("forbidden", body.decode("utf-8").lower())

    def test_api_status_whitelisted_no_token_leakage(self):
        with patch("wechat_tracker.console.handler.read_status") as mock_st:
            mock_st.return_value = {
                "instance": "abc12345",
                "running": True,
                "status": "monitoring",
                "pid": 5566,
                "control_port": 12345,
                "control_token": "super_secret_token",
            }
            status, _, body = self._get("/api/status")
            self.assertEqual(status, 200)
            data = json.loads(body.decode("utf-8"))
            self.assertTrue(data.get("ok"))
            svc = data.get("service", {})
            self.assertEqual(svc.get("pid"), 5566)
            self.assertNotIn("control_token", svc)
            self.assertNotIn("control_port", svc)

    def test_api_databases_excludes_backups(self):
        # Create a fake backup folder inside temp base
        backup_dir = self.base_path / "state" / "web-repair-backup-20261016-174725"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_db = backup_dir / "tracker.sqlite3"
        backup_db.touch()

        status, _, body = self._get("/api/databases")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        paths = [d["path"] for d in data.get("databases", [])]
        for p in paths:
            self.assertNotIn("web-repair-backup", p)

    def test_api_events_and_outbox(self):
        status, _, body = self._get("/api/events")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data.get("events", [])), 1)
        self.assertEqual(data["events"][0]["title"], "量子计算前沿讲座")

        status, _, body = self._get("/api/outbox")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data.get("jobs", [])), 1)

    def test_api_preview_email(self):
        status, _, body = self._get("/api/preview_email?event_id=test-event-001")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertIn("量子计算前沿讲座", data["subject"])
        self.assertEqual(data["body"], "【活动通知】量子计算前沿讲座...")
        self.assertNotIn("报名开始时间", data["body"])
        self.assertNotIn("https://example.com/signup", data["body"])
        self.assertIn("<h1", data["html_body"])
        self.assertIn("量子计算前沿讲座", data["html_body"])

    def test_api_calendar_export(self):
        status, headers, body = self._get("/api/calendar/export")
        self.assertEqual(status, 200)
        self.assertIn("text/calendar", headers.get("Content-Type"))
        content = body.decode("utf-8")
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("量子计算前沿讲座", content)
        self.assertIn("END:VCALENDAR", content)

    @patch("wechat_tracker.console.handler.probe")
    def test_api_group_probe(self, mock_probe):
        mock_probe.return_value = {"ok": True, "status": "ready", "group": "邮件测试群"}
        from urllib.parse import urlencode
        status, _, body = self._get("/api/group/probe?" + urlencode({"group": "邮件测试群"}))
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertTrue(data["probe"]["ok"])
        self.assertIn("就绪", data.get("message", ""))

    @patch("wechat_tracker.console.handler.probe")
    def test_group_switch_aborts_when_probe_fails(self, mock_probe):
        mock_probe.return_value = {"ok": False, "status": "window_not_found"}
        status, _, body = self._post("/api/group/switch", {"group": "不存在的群"})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data.get("ok"))
        self.assertIn("保留原群配置", data.get("error", ""))

        # Verify config was not touched
        raw = self.config_path.read_text(encoding="utf-8")
        self.assertIn('group = "测试讲座通知群"', raw)

    @patch("wechat_tracker.console.handler.probe")
    @patch("wechat_tracker.console.handler.stop_service")
    def test_group_switch_success_and_switch_back(self, mock_stop, mock_probe):
        mock_probe.return_value = {"ok": True, "status": "ready"}
        mock_stop.return_value = {"running": False, "status": "stopped"}

        # 1. Switch to a new group: 计算机前沿交流群
        status, _, body = self._post("/api/group/switch", {"group": "计算机前沿交流群"})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["group"], "计算机前沿交流群")
        new_dir_1 = data["data_dir"]

        # Verify config preserved comments and root keys updated
        content = self.config_path.read_text(encoding="utf-8")
        self.assertIn('group = "计算机前沿交流群"', content)
        self.assertIn('# 系统主配置文件', content)
        self.assertIn('# 仅发现通知，报名由用户核对名单', content)
        self.assertIn('[service]', content)

        # 2. Switch back to original group
        status, _, body = self._post("/api/group/switch", {"group": "测试讲座通知群"})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["group"], "测试讲座通知群")
        # Ensure it found original data_dir
        self.assertEqual(Path(data["data_dir"]).resolve(), self.data_dir.resolve())

        # Ensure original tracker database still exists with events
        self.assertTrue(self.db_path.is_file())
        store = Store(self.db_path)
        try:
            self.assertEqual(len(store.events()), 1)
        finally:
            store.close()

    @patch("wechat_tracker.console.handler.read_status")
    @patch("wechat_tracker.console.handler.start_service")
    def test_service_mode_switching_start(self, mock_start, mock_read):
        # Service currently running in send mode
        mock_read.return_value = {"running": True, "send_enabled": True, "status": "monitoring"}
        mock_start.return_value = {"running": True, "send_enabled": False, "status": "monitoring"}

        # /api/service/start should restart into preview mode (send=False)
        with patch("wechat_tracker.console.handler.stop_service") as mock_stop:
            status, _, body = self._post("/api/service/start")
            self.assertEqual(status, 200)
            mock_stop.assert_called_once()
            mock_start.assert_called_with(str(self.config_path.resolve()), self.server.config, send=False)

    @patch("wechat_tracker.console.handler.read_status")
    @patch("wechat_tracker.console.handler.start_service")
    @patch("wechat_tracker.console.handler.stop_service")
    def test_service_restart_preserves_mode(self, mock_stop, mock_start, mock_read):
        mock_read.return_value = {"running": True, "send_enabled": True, "status": "monitoring"}
        mock_start.return_value = {"running": True, "send_enabled": True, "status": "monitoring"}

        status, _, body = self._post("/api/service/restart", {})
        self.assertEqual(status, 200)
        mock_start.assert_called_with(str(self.config_path.resolve()), self.server.config, send=True)

    def test_multi_services_list_and_stop(self):
        # Create a simulated sibling service in state
        sub_svc_dir = self.base_path / "state" / "sibling-tracker"
        sub_svc_dir.mkdir(parents=True, exist_ok=True)
        (sub_svc_dir / "service.json").write_text(
            json.dumps({
                "instance": "inst-999",
                "running": True,
                "status": "monitoring",
                "pid": 99999,
                "group": "学术通知测试群",
                "control_port": 54321,
                "control_token": "secret_token_999",
            }),
            encoding="utf-8"
        )

        with patch("wechat_tracker.console.handler.read_status") as mock_read:
            def _fake_read(cfg):
                if "sibling-tracker" in cfg.get("data_dir", ""):
                    return {
                        "instance": "inst-999",
                        "running": True,
                        "status": "monitoring",
                        "pid": 99999,
                        "group": "学术通知测试群",
                        "control_token": "should_be_stripped",
                    }
                return {"running": False, "status": "stopped"}

            mock_read.side_effect = _fake_read
            status, _, body = self._get("/api/services")
            self.assertEqual(status, 200)
            data = json.loads(body.decode("utf-8"))
            self.assertTrue(data.get("ok"))
            svcs = data.get("services", [])
            self.assertTrue(len(svcs) >= 2)
            sibling = next((s for s in svcs if "sibling-tracker" in s.get("data_dir", "")), None)
            self.assertIsNotNone(sibling)
            self.assertEqual(sibling["group"], "学术通知测试群")
            self.assertNotIn("control_token", sibling)

        # Test stopping other service
        with patch("wechat_tracker.console.handler.stop_service") as mock_stop:
            mock_stop.return_value = {"running": False, "status": "stopped"}
            status, _, body = self._post("/api/services/stop", {"data_dir": str(sub_svc_dir)})
            self.assertEqual(status, 200)
            data = json.loads(body.decode("utf-8"))
            self.assertTrue(data.get("ok"))
            mock_stop.assert_called_once()


class ConfigResolutionUnitTests(unittest.TestCase):
    def test_update_toml_root_keys_preserves_comments_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_file = Path(tmp_dir) / "config.toml"
            original_text = (
                '# Top level header\n'
                'group = "旧群名"\n'
                'data_dir = "/tmp/old-dir"\n'
                '# Middle comment\n'
                'timezone = "Asia/Shanghai"\n'
                'reminder_minutes = []\n\n'
                '[capture]\n'
                '# Capture comment\n'
                'source = "macos_ax"\n'
            )
            cfg_file.write_text(original_text, encoding="utf-8")

            old, new = update_toml_root_keys(cfg_file, {"group": "新群名", "data_dir": "/tmp/new-dir"})
            self.assertIn('group = "旧群名"', old)
            self.assertIn('group = "新群名"', new)
            self.assertIn('data_dir = "/tmp/new-dir"', new)
            self.assertIn('# Top level header', new)
            self.assertIn('# Middle comment', new)
            self.assertIn('# Capture comment', new)
            self.assertIn('source = "macos_ax"', new)

    def test_resolve_group_data_dir_stable_and_recovers_old(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir) / "state"
            base.mkdir()

            # First time resolving Group X
            dir_x = resolve_group_data_dir(base, "研一通知群")
            self.assertTrue(dir_x.is_dir())
            (dir_x / "service.json").write_text(json.dumps({"group": "研一通知群"}), encoding="utf-8")

            # Resolving Group Y
            dir_y = resolve_group_data_dir(base, "学术讲座群")
            self.assertTrue(dir_y.is_dir())
            self.assertNotEqual(dir_x, dir_y)

            # Resolving Group X again recovers dir_x
            dir_x2 = resolve_group_data_dir(base, "研一通知群")
            self.assertEqual(dir_x, dir_x2)


if __name__ == "__main__":
    unittest.main()
