"""Local dashboard transport: narrow routes and same-origin JSON mutations."""

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import mimetypes
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse, parse_qs
from . import __version__
from .capture import default_capture

ASSETS = Path(__file__).parent / "resources" / "dashboard"


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, application):
        if address[0] != "127.0.0.1":
            raise ValueError("Dashboard仅允许监听127.0.0.1")
        self.app = application
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def trusted(self):
        try:
            host = urlparse("http://" + self.headers.get("Host", ""))
            if (
                host.hostname not in {"127.0.0.1", "localhost"}
                or host.port != self.server.server_port
                or host.username
                or host.password
            ):
                return False
            for field in ("Origin", "Referer"):
                if value := self.headers.get(field):
                    origin = urlparse(value)
                    if (
                        origin.scheme != "http"
                        or origin.hostname != host.hostname
                        or origin.port != host.port
                        or origin.username
                        or origin.password
                    ):
                        return False
            return True
        except ValueError:
            return False

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, mutation):
        if not self.trusted():
            return self.send_json({"ok": False, "error": "拒绝跨站或非本机请求"}, 403)
        app = self.server.app
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        query = parse_qs(parsed.query)
        body = {}
        try:
            if mutation:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("修改请求须为application/json")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 262144:
                    raise ValueError("请求正文须为1至262144字节")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("请求须为JSON对象")
            if parts == ["api", "bootstrap"] and not mutation:
                return self.send_json(
                    dict(
                        ok=True,
                        windows=app.windows(),
                        model=app.settings.public(),
                        version=__version__,
                        platform=sys.platform,
                        default_capture=default_capture(),
                    )
                )
            if parts == ["api", "model"] and mutation:
                return self.send_json(dict(ok=True, model=app.settings.save(body)))
            if parts == ["api", "windows"] and mutation:
                return self.send_json(dict(ok=True, window=app.save_window(body)), 201)
            if len(parts) >= 3 and parts[:2] == ["api", "windows"]:
                identity = parts[2]
                app.require_window(identity)
                action = (
                    parts[3] if len(parts) == 4 else "" if len(parts) == 3 else None
                )
                if action == "" and mutation:
                    return self.send_json(
                        dict(ok=True, window=app.save_window(body, identity))
                    )
                if action == "rules" and not mutation:
                    return self.send_json(dict(ok=True, rules=app.repo.rules(identity)))
                if action == "rules" and mutation:
                    return self.send_json(
                        dict(ok=True, rule=app.save_rule(identity, body["spec"]))
                    )
                if action == "generate" and mutation:
                    return self.send_json(dict(ok=True, rule=app.generate(identity)))
                if action == "rule-prompt" and not mutation:
                    return self.send_json(
                        dict(ok=True, **app.export_rule_prompt(identity))
                    )
                if action == "import-rule" and mutation:
                    return self.send_json(
                        dict(
                            ok=True,
                            rule=app.import_rule(
                                identity, body["content"], body["revision"]
                            ),
                        )
                    )
                if action == "activate" and mutation:
                    return self.send_json(
                        dict(ok=True, window=app.activate(identity, body["rule_id"]))
                    )
                if action == "test" and mutation:
                    return self.send_json(
                        dict(
                            ok=True,
                            result=app.test(identity, body["spec"], body["message"]),
                        )
                    )
                if action == "start" and mutation:
                    return self.send_json(dict(ok=True, runtime=app.start(identity)))
                if action == "stop" and mutation:
                    return self.send_json(
                        dict(ok=True, runtime=app.runtime.stop(identity))
                    )
                if action == "probe" and mutation:
                    return self.send_json(dict(ok=True, probe=app.probe(identity)))
                if action == "retry" and mutation:
                    return self.send_json(
                        dict(ok=True, retried=app.repo.retry(identity))
                    )
                if action == "records" and not mutation:
                    limit = int(query.get("limit", ["50"])[0])
                    before = int(query["before"][0]) if "before" in query else None
                    search = query.get("q", [""])[0]
                    category = query.get("category", ["all"])[0]
                    if (
                        not 1 <= limit <= 100
                        or (
                            before is not None and not 0 < before <= 9223372036854775807
                        )
                        or len(search) > 500
                        or category not in {"all", "matched", "error"}
                    ):
                        raise ValueError("查询条件无效")
                    return self.send_json(
                        dict(
                            ok=True,
                            **app.repo.records(
                                identity,
                                before=before,
                                limit=limit,
                                search=search,
                                category=category,
                            ),
                        )
                    )
            if not mutation and parts[0] != "api":
                name = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                path = (ASSETS / name).resolve()
                if (
                    not path.is_relative_to(ASSETS.resolve())
                    or not path.is_file()
                    or path.suffix not in {".html", ".css", ".js", ".svg"}
                ):
                    return self.send_json({"ok": False, "error": "文件不存在"}, 404)
                content = path.read_bytes()
                self.send_response(200)
                mime = (
                    "application/javascript"
                    if path.suffix == ".js"
                    else mimetypes.guess_type(name)[0]
                )
                self.send_header(
                    "Content-Type",
                    (mime or "application/octet-stream") + "; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(content)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
                )
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
                return
            return self.send_json({"ok": False, "error": "接口不存在"}, 404)
        except sqlite3.IntegrityError:
            return self.send_json(
                {"ok": False, "error": "该精确名称已存在，或关联数据无效"}, 409
            )
        except (ValueError, KeyError, TypeError):
            # Only use our controlled validation messages, never upstream response bodies or inputs.
            exc = sys.exception()
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                and not isinstance(exc, json.JSONDecodeError)
                else "请求字段或格式无效"
            )
            if len(message) > 300:
                message = "请求校验失败"
            return self.send_json({"ok": False, "error": message}, 400)
        except Exception:
            return self.send_json(
                {"ok": False, "error": "操作失败，已保存的数据保留，请重试"}, 500
            )
