"""Stored capture evidence is readable without changing capture or mail state."""

from datetime import datetime
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from console_fixtures import isolated_console

from wechat_tracker.config import load_config
from wechat_tracker.engine import Engine
from wechat_tracker.store import Store
from wechat_tracker.web import WebControlHandler, WebControlServer


class CapturedMessagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        isolation = isolated_console(cls.root)
        isolation.__enter__()
        cls.addClassCleanup(isolation.__exit__, None, None, None)
        cls.config_path = cls.root / "config.toml"
        cls.config_path.write_text(f'group = "alpha"\ndata_dir = {json.dumps(str(cls.root / "data"))}\nreminder_minutes = []\n')
        cls.db_path = cls.root / "data/tracker.sqlite3"
        store = Store(cls.db_path)
        engine = Engine(store, load_config(cls.config_path))
        fixtures = [
            {"id": "normal", "sender": "小林", "kind": "text", "text": "今晚班会调整至线上\n请查看课表 <script>alert(1)</script>",
             "sent_at": "2026-10-17T11:00:00+08:00", "source_meta": {"private_transport": "hidden"}},
            {"id": "lecture", "sender": "小张", "kind": "text", "text": "讲座通知：人工智能学术报告"},
            {"id": "parent", "sender": "转发者", "kind": "forward", "text": "聊天记录", "children": [
                {"text": "报告主题：人工智能学术讲座", "sender": "原作者"}]},
            {"id": "card", "kind": "forward", "text": "小林和小张的聊天记录\n周末安排"},
            {"id": "normal", "text": "另一条相同 ID 的消息"},
        ]
        for index, message in enumerate(fixtures):
            engine.ingest(message, datetime.fromisoformat(f"2026-10-17T12:00:0{index}+08:00"))
        store.db.execute("INSERT INTO capture_state VALUES (?,?)", ("alpha", "2026-10-17T04:00:10Z"))
        store.db.execute("INSERT INTO messages VALUES (?,?,?)", ("broken", "bad-json", "2026-10-17T03:00:00Z"))
        store.db.commit()
        store.close()
        cls.before = cls.db_path.read_bytes()
        cls.server = WebControlServer(("127.0.0.1", 0), WebControlHandler, cls.config_path)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def get(self, **params):
        request = urllib.request.Request(self.url + "/api/messages?" + urllib.parse.urlencode(params))
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_messages_include_non_lectures_and_use_ingestion_order(self):
        status, data = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data["counts"], {"all": 5, "non_lecture": 3, "lecture": 2})
        self.assertEqual([item["id"] for item in data["messages"]], ["normal", "card", "parent", "lecture", "normal"])
        self.assertEqual(data["latest_received_at"], "2026-10-17T04:00:04+00:00")
        self.assertEqual(data["last_success_at"], "2026-10-17T04:00:10Z")
        self.assertEqual(data["unreadable_count"], 1)

    def test_filters_use_actual_event_links_including_forward_children(self):
        _, data = self.get(category="lecture")
        self.assertEqual({item["id"] for item in data["messages"]}, {"lecture", "parent"})
        _, data = self.get(category="non_lecture")
        self.assertEqual(data["total"], 3)
        card = next(item for item in data["messages"] if item["id"] == "card")
        self.assertEqual(card["children"], [])
        self.assertNotIn("sent_at", card)

    def test_search_precedes_pagination_and_includes_sender_and_nested_text(self):
        _, data = self.get(q="小林", limit=1)
        self.assertEqual(data["total"], 2)
        self.assertTrue(data["has_more"])
        _, second = self.get(q="小林", limit=1, offset=1)
        self.assertFalse(second["has_more"])
        self.assertEqual(second["messages"][0]["sender"], "小林")
        _, nested = self.get(q="原作者")
        self.assertEqual(nested["total"], 1)
        self.assertEqual(nested["messages"][0]["id"], "parent")
        self.assertEqual(self.get(q="received_at")[1]["total"], 0)

    def test_new_nonlecture_capture_appears_on_next_read_without_duplicate_or_mail(self):
        path = self.root / "fresh.sqlite3"
        store = Store(path)
        try:
            engine = Engine(store, load_config(self.config_path))
            baseline = {"id": "baseline", "kind": "text", "text": "已有消息"}
            message = {"id": "new", "kind": "text", "text": "刚刚发布的课表更新"}
            engine.capture_snapshot([baseline])
            self.assertEqual(self.get(db=str(path))[1]["total"], 0)
            engine.capture_snapshot([baseline, message])
            engine.capture_snapshot([baseline, message])
            before = path.read_bytes()
            _, data = self.get(db=str(path))
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["messages"][0]["text"], message["text"])
            self.assertEqual(data["messages"][0]["category"], "non_lecture")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 0)
            self.assertEqual(path.read_bytes(), before)
        finally:
            store.close()

    def test_read_whitelist_retains_original_text_without_transport_metadata(self):
        _, data = self.get(q="课表")
        message = data["messages"][0]
        self.assertIn("\n请查看课表 <script>alert(1)</script>", message["text"])
        self.assertNotIn("source_meta", message)
        self.assertNotIn("private_transport", json.dumps(data))
        self.assertEqual(self.db_path.read_bytes(), self.before)

    def test_invalid_pagination_category_and_unauthorized_path_are_rejected(self):
        for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"limit": "oops"}, {"category": "unknown"}):
            with self.subTest(params=params):
                self.assertEqual(self.get(**params)[0], 400)
        self.assertEqual(self.get(db="/etc/private.sqlite3")[0], 403)

    def test_selected_history_with_no_capture_tables_and_missing_active_db(self):
        legacy = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
        _, data = self.get(db=str(legacy))
        self.assertEqual(data["messages"], [])
        self.assertIsNone(data["last_success_at"])
        old_config = self.server.config
        try:
            self.server.config = dict(old_config, data_dir=str(self.root / "absent"))
            status, data = self.get()
            self.assertEqual(status, 200)
            self.assertEqual(data["total"], 0)
            self.assertFalse((self.root / "absent").exists())
        finally:
            self.server.config = old_config
