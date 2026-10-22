"""Real localhost HTTP and model transport, fake capture, isolated data only."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from wechat_monitor.application import Application
from wechat_monitor.model import ChatClient
from wechat_monitor.web import MonitorServer
from test_monitor_core import FakeCapture, FakeModel, local_rule


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class MonitorWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = FakeModel()
        self.capture = FakeCapture()
        self.app = Application(self.temp.name, model=self.model, capture=self.capture)
        self.addCleanup(self.app.close)
        self.server = MonitorServer(("127.0.0.1", 0), self.app)
        self.scope = running(self.server)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=dict(
                {"Content-Type": "application/json"} if body is not None else {},
                **(headers or {}),
            ),
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def json(self, path, body=None, headers=None):
        code, _, raw = self.request(path, body, headers)
        return code, json.loads(raw)

    def create(self, name="任意私聊窗口"):
        code, data = self.json(
            "/api/windows",
            dict(
                name=name,
                target="构建失败",
                mode="local",
                include_existing=True,
                capture={"source": "windows_uia"},
            ),
        )
        self.assertEqual(code, 201)
        return data["window"]

    def test_real_http_rule_generation_activation_testing_and_no_side_effects(self):
        window = self.create()
        prefix = "/api/windows/" + window["id"]
        code, data = self.json(prefix + "/generate", {})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.model.calls), 1)
        rule = data["rule"]
        self.assertEqual(
            self.json(prefix + "/activate", {"rule_id": rule["id"]})[0], 200
        )
        code, data = self.json(
            prefix + "/test", dict(spec=rule["spec"], message=dict(text="项目构建失败"))
        )
        self.assertTrue(data["result"]["matched"])
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.json(prefix + "/records")[1]["records"], [])
        self.assertEqual(
            self.json(prefix + "/probe", {})[1]["probe"]["window_name"], window["name"]
        )
        other = self.create("另一个窗口")
        self.assertEqual(
            self.json(
                "/api/windows/" + other["id"] + "/activate", {"rule_id": rule["id"]}
            )[0],
            400,
        )

    def test_origin_host_content_type_and_unknown_routes_are_rejected(self):
        self.assertEqual(
            self.json(
                "/api/bootstrap", headers={"Origin": "https://untrusted.example"}
            )[0],
            403,
        )
        self.assertEqual(
            self.json("/api/bootstrap", headers={"Host": "untrusted.example"})[0], 403
        )
        self.assertEqual(
            self.json("/api/model", {}, headers={"Content-Type": "text/plain"})[0], 400
        )
        self.assertEqual(self.json("/api/nope")[0], 404)
        self.assertEqual(self.json("/../pyproject.toml")[0], 404)
        self.assertEqual(self.json("/api/bootstrap", {})[0], 404)

    def test_duplicate_names_and_invalid_configuration(self):
        self.create()
        self.assertEqual(
            self.json(
                "/api/windows",
                dict(
                    name="任意私聊窗口", target="x", capture={"source": "windows_uia"}
                ),
            )[0],
            409,
        )
        for body in (
            dict(name="x", target="x", interval=0),
            dict(name="x", target="x", mode="exec"),
            dict(name="x", target="x", capture={"source": "jsonl"}),
        ):
            self.assertEqual(self.json("/api/windows", body)[0], 400)

    def test_bootstrap_never_contains_secrets_or_reads_other_roots(self):
        with patch.dict("os.environ", {}, clear=True):
            self.json(
                "/api/model", dict(model="test", api_key="synthetic-secret-token")
            )
            _, data = self.json("/api/bootstrap")
            self.assertTrue(data["model"]["key_configured"])
            self.assertNotIn("synthetic-secret-token", json.dumps(data))
            self.assertEqual(data["windows"], [])

    def test_cursor_validation_and_data_isolation(self):
        one = self.create()
        two = self.create("two")
        rule = self.app.save_rule(one["id"], local_rule())
        self.app.activate(one["id"], rule["id"])
        self.app.repo.ingest(
            self.app.require_window(one["id"]),
            [dict(text="构建失败", id=str(i)) for i in range(4)],
        )
        prefix = "/api/windows/" + one["id"] + "/records"
        first = self.json(prefix + "?limit=2")[1]
        second = self.json(
            prefix + "?before=" + str(first["next_before"]) + "&limit=2"
        )[1]
        self.assertEqual(len(first["records"]) + len(second["records"]), 4)
        self.assertEqual(
            self.json("/api/windows/" + two["id"] + "/records")[1]["records"], []
        )
        for query in (
            "limit=0",
            "before=-1",
            "category=unknown",
            "before=9223372036854775808",
        ):
            self.assertEqual(self.json(prefix + "?" + query)[0], 400)

    def test_model_adapter_uses_real_json_http_without_native_or_secret_output(self):
        received = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": json.dumps(
                                            {
                                                "instructions": "keyword",
                                                "match": local_rule()["match"],
                                            }
                                        )
                                    }
                                }
                            ]
                        }
                    ).encode()
                )

        with running(ThreadingHTTPServer(("127.0.0.1", 0), Provider)) as provider:
            self.app.settings.save(
                dict(
                    base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                    model="fixture",
                )
            )
            self.app.model = ChatClient(self.app.settings)
            one = self.create()
            rule = self.app.generate(one["id"])
        self.assertEqual(rule["spec"]["target"], "构建失败")
        self.assertEqual(len(received), 1)
        self.assertEqual(
            json.loads(received[0]["messages"][1]["content"])["mode"], "local"
        )
