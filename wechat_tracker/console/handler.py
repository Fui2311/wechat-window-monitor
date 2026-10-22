"""HTTP routes and request validation for the local console."""
from __future__ import annotations
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
import json
import mimetypes
import os
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlparse

from ..calendar import export_ics
from ..capture import probe
from ..config import atomic_write_config, load_config, resolve_config_path, resolve_group_data_dir, update_toml_root_keys
from ..engine import Engine
from ..notify import format_notification, format_notification_html, notification_format
from ..service import read_status, reset_test_data, start_service, stop_service
from ..store import Store
from ..processing import extract_llm, prepare_settings, public_settings, save_settings
from ..extraction.settings import effective_processing_config

from ..paths import PROJECT_ROOT, RESOURCE_ROOT
from .discovery import _list_services
from .presentation import _delivery_info, _sanitize_service
from .queries import (_captured_messages, _list_databases, calendar_events,
                      delivery_backlog, read_events, read_jobs, read_preview_target)

WEB_RESOURCES_DIR = RESOURCE_ROOT / "web"
_OPERATION_LOCK = threading.Lock()

class WebControlHandler(BaseHTTPRequestHandler):
    # Idle browser connections must not hold graceful console shutdown open.
    timeout = 5

    def log_message(self, format, *args):
        # Suppress repetitive status poll logs
        if args and str(args[0]).startswith("GET /api/status"):
            return
        super().log_message(format, *args)

    @property
    def config(self) -> dict:
        return self.server.config

    @property
    def config_path(self) -> str:
        return self.server.config_path

    def _check_origin(self) -> bool:
        host_hdr = self.headers.get("Host", "")
        try:
            host = urlparse("http://" + host_hdr)
            valid_host = (host.hostname in {"localhost", "127.0.0.1", "::1"}
                          and (host.port or 80) == self.server.server_port
                          and not host.username and not host.password)
        except ValueError:
            valid_host = False
        if not valid_host:
            self.send_error(400, "Bad Request: Untrusted Host header")
            return False
        for name in ("Origin", "Referer"):
            value = self.headers.get(name)
            if not value:
                continue
            try:
                origin = urlparse(value)
                same_origin = (origin.scheme == "http" and origin.hostname == host.hostname
                               and (origin.port or 80) == self.server.server_port
                               and not origin.username and not origin.password)
            except ValueError:
                same_origin = False
            if not same_origin:
                self._send_error("Forbidden: Cross-site request rejected", 403)
                return False
        return True

    def _send_json(self, data: dict, status: int = 200):
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, message: str, status: int = 400):
        self._send_json({"ok": False, "error": message}, status=status)

    def _serve_file(self, file_path: Path):
        if not file_path.is_file():
            self.send_error(404, "File Not Found")
            return
        if file_path.suffix == ".js":
            mime_type = "application/javascript; charset=utf-8"
        elif file_path.suffix == ".css":
            mime_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".html":
            mime_type = "text/html; charset=utf-8"
        else:
            mime_type, _ = mimetypes.guess_type(str(file_path))
            mime_type = mime_type or "application/octet-stream"
            if mime_type.startswith("text/") or mime_type in {"application/javascript", "application/json"}:
                mime_type += "; charset=utf-8"

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _allowed_roots(self) -> list[Path]:
        data_dir = Path(self.config["data_dir"]).resolve()
        roots = [
            data_dir,
            data_dir.parent,
            PROJECT_ROOT / "state",
            Path.home() / "Library" / "Application Support" / "WeChatLectureTracker",
        ]
        roots.extend(Path(svc["data_dir"]).resolve() for svc in _list_services(self.config, self.config_path)
                     if svc.get("data_dir"))
        return [r for r in roots if r.exists()]

    def _resolve_db_path(self, query_params: dict) -> Path:
        db_param = query_params.get("db", [None])[0]
        if not db_param:
            return Path(self.config["data_dir"]).resolve() / "tracker.sqlite3"

        target = Path(db_param).resolve()
        if target.suffix.lower() != ".sqlite3":
            raise ValueError("Invalid SQLite database path: must end with .sqlite3")

        allowed_roots = self._allowed_roots()
        if not any(target == r or r in target.parents for r in allowed_roots):
            raise PermissionError("Access to database outside authorized directory is forbidden")

        # The UI sends the selected active path even before the first capture.
        # Return empty views for that database; never create it on a read.
        active_db = Path(self.config["data_dir"]).resolve() / "tracker.sqlite3"
        if not target.is_file() and target != active_db:
            raise FileNotFoundError(f"Database not found: {target}")

        return target

    def _delivery_service(self, db_path):
        if db_path.name == "demo.sqlite3":
            return {"simulation": True, "group": "Demo 演示"}
        return next((svc for svc in _list_services(self.config, self.config_path)
                     if (Path(svc.get("data_dir", "")) / "tracker.sqlite3").resolve() == db_path.resolve()), {})

    def do_OPTIONS(self):
        if not self._check_origin():
            return
        self.send_response(204)
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self):
        if not self._check_origin():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}:
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        mime_type = "text/html; charset=utf-8"
        if path.endswith(".css"):
            mime_type = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            mime_type = "application/javascript; charset=utf-8"
        elif path.startswith("/api/"):
            mime_type = "application/json; charset=utf-8"
        self.send_header("Content-Type", mime_type)
        self.end_headers()

    def do_GET(self):
        if not self._check_origin():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            # 1. Static Assets
            if path in {"/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}:
                self.send_response(204)
                self.end_headers()
                return
            if path in {"/", "/index.html"}:
                return self._serve_file(WEB_RESOURCES_DIR / "index.html")
            if path == "/app.css":
                return self._serve_file(WEB_RESOURCES_DIR / "app.css")
            if path == "/app.js":
                return self._serve_file(WEB_RESOURCES_DIR / "app.js")

            # 2. API Endpoints
            if path == "/api/processing":
                return self._send_json({"ok": True, "processing": public_settings(self.config)})
            if path == "/api/status":
                status = _sanitize_service(read_status(self.config))
                # Status reads must not load Keychain secrets into this web
                # process or carry credentials from a previously selected group.
                from ..credentials import credential_status
                email_status = credential_status(self.config)

                safe_cfg = {
                    "group": self.config.get("group"),
                    "timezone": self.config.get("timezone", "Asia/Shanghai"),
                    "reminder_minutes": self.config.get("reminder_minutes", []),
                    "max_attempts": self.config.get("max_attempts", 5),
                    "data_dir": self.config.get("data_dir"),
                    "capture_source": self.config.get("capture", {}).get("source", "macos_ax"),
                    "processing": public_settings(self.config),
                    "email": {
                        "host": self.config.get("email", {}).get("smtp_host", "smtp.qq.com"),
                        "port": self.config.get("email", {}).get("smtp_port", 465),
                        "security": self.config.get("email", {}).get("smtp_security", "ssl"),
                        "from": os.environ.get(self.config.get("email", {}).get("from_env", "WECHAT_EMAIL_FROM"), "未配置"),
                    },
                }

                return self._send_json({
                    "ok": True,
                    "service": status,
                    "config": safe_cfg,
                    "email_configured": email_status["configured"],
                    "email_status": email_status,
                })

            if path == "/api/databases":
                databases = _list_databases(self.config["data_dir"], self.config.get("group", ""),
                                           _list_services(self.config, self.config_path))
                return self._send_json({"ok": True, "databases": databases})

            if path == "/api/services":
                services = _list_services(self.config, self.config_path)
                return self._send_json({"ok": True, "services": services})

            if path == "/api/service/backlog":
                db_path = Path(self.config["data_dir"]).resolve() / "tracker.sqlite3"
                return self._send_json(dict(ok=True, group=self.config.get("group", ""), db=str(db_path),
                                            **delivery_backlog(db_path, datetime.now(timezone.utc).isoformat())))

            if path == "/api/events":
                db_path = self._resolve_db_path(query)
                if not db_path.is_file():
                    return self._send_json({"ok": True, "events": [], "db": str(db_path)})

                events = read_events(db_path, self._delivery_service(db_path))
                return self._send_json({"ok": True, "events": events, "db": str(db_path)})

            if path == "/api/messages":
                db_path = self._resolve_db_path(query)
                category = query.get("category", ["all"])[0]
                if category not in {"all", "non_lecture", "lecture"}:
                    raise ValueError("Invalid message category")
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                if not 1 <= limit <= 500 or offset < 0:
                    raise ValueError("Message limit must be 1–500 and offset must be nonnegative")
                data = _captured_messages(db_path, category=category,
                                          search=query.get("q", [""])[0].strip(), limit=limit, offset=offset)
                return self._send_json(dict(data, ok=True, db=str(db_path)))

            if path == "/api/outbox":
                db_path = self._resolve_db_path(query)
                if not db_path.is_file():
                    return self._send_json({"ok": True, "jobs": [], "db": str(db_path)})

                delivery_service = self._delivery_service(db_path)
                jobs = read_jobs(db_path, delivery_service)
                return self._send_json({"ok": True, "jobs": jobs, "db": str(db_path),
                                        "delivery_service": delivery_service})

            if path == "/api/preview_email":
                db_path = self._resolve_db_path(query)
                event_id = query.get("event_id", [None])[0]
                job_id = query.get("job_id", [None])[0]

                if not db_path.is_file():
                    return self._send_error("Database file not found", 404)
                target_event, target_reason = read_preview_target(db_path, event_id, job_id)

                if not target_event:
                    return self._send_error("Event or Job not found", 404)

                if target_reason not in {"new", "update", "cancelled", "reminder", "review"}:
                    target_reason = "new"

                try:
                    subject, body = format_notification(target_event, target_reason)
                    html_body = format_notification_html(target_event, target_reason)
                except Exception as e:
                    return self._send_error(f"无法生成邮件预览: {e}", 400)
                return self._send_json({"ok": True, "subject": subject, "body": body, "html_body": html_body,
                                        "mail_format": notification_format(target_event)})

            if path == "/api/calendar/export":
                db_path = self._resolve_db_path(query)
                events = calendar_events(db_path)

                ics_content = export_ics(events).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="lectures.ics"')
                self.send_header("Content-Length", str(len(ics_content)))
                self.end_headers()
                self.wfile.write(ics_content)
                return

            if path == "/api/logs":
                lines_req = int(query.get("lines", [200])[0])
                log_file = Path(self.config["data_dir"]) / "service.log"
                lines = []
                if log_file.is_file():
                    try:
                        all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                        lines = all_lines[-lines_req:]
                    except Exception as e:
                        lines = [f"读取日志出错: {e}"]
                else:
                    lines = ["日志文件尚不存在 (服务启动后将写入 service.log)"]
                return self._send_json({"ok": True, "lines": lines})

            if path == "/api/probe":
                result = probe(self.config)
                return self._send_json({"ok": True, "probe": result})

            if path == "/api/group/probe":
                candidate_group = query.get("group", [None])[0]
                if not candidate_group or not candidate_group.strip():
                    return self._send_error("必须指定待探测的群名称", 400)
                candidate_config = dict(self.config, group=candidate_group.strip())
                result = probe(candidate_config)
                is_ok = bool(result.get("ok"))
                msg = "目标群窗口已就绪并可读" if is_ok else "未探测到目标微信群独立聊天窗口，请在微信中打开该群窗口并保持可见"
                return self._send_json({"ok": is_ok, "probe": result, "message": msg})

            return self._send_error("未知查询接口", 404)

        except PermissionError as p_err:
            self._send_error(str(p_err), 403)
        except (ValueError, FileNotFoundError) as req_err:
            self._send_error(str(req_err), 400 if isinstance(req_err, ValueError) else 404)
        except Exception as error:
            self._send_error(str(error), 500)

    def _pause_service_startup(self, config, config_path=None):
        if sys.platform in {"darwin", "win32"} and config_path:
            from ..autostart import manage_startup
            return manage_startup(config_path, config, "pause")
        return {"installed": False}

    def do_POST(self):
        if not self._check_origin():
            return
        path = urlparse(self.path).path
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if not 0 <= content_length <= 65536:
                raise ValueError("请求内容过大")
            body = json.loads(self.rfile.read(content_length)) if content_length else {}
            if not isinstance(body, dict):
                raise ValueError("请求必须为 JSON 对象")

            if path == "/api/processing":
                with _OPERATION_LOCK:
                    # Read latest file so a settings save cannot revert a group switch.
                    self.server.config = save_settings(load_config(self.config_path), body)
                    return self._send_json({"ok": True, "processing": public_settings(self.config),
                                           "effective": "next_message"})

            if path == "/api/processing/test":
                config = prepare_settings(effective_processing_config(load_config(self.config_path)), body)
                text = body.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                    raise ValueError("请输入 1–10000 字的测试原文")
                # Explicit user-entered text only: no database, outbox, or capture access.
                message = {"id": "llm-test", "group": config["group"], "text": text}
                key = body.get("api_key", "").strip() or None
                if body.get("clear_api_key"):
                    key = os.environ.get(config.get("llm_api_key_env", "WECHAT_LLM_API_KEY"), "")
                events = extract_llm(message, config, key=key)
                return self._send_json({"ok": True, "events": events, "stored": False, "sent": False})

            if path == "/api/console/stop":
                with _OPERATION_LOCK:
                    # Deliver the acknowledgement before stopping the HTTP loop.
                    # The independent monitor and its startup entry are untouched.
                    self._send_json({"ok": True, "console": "stopping", "monitor_unchanged": True})
                    self.wfile.flush()
                    if not self.server.shutdown_requested.is_set():
                        self.server.shutdown_requested.set()
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            if path == "/api/email/authorize":
                from ..credentials import authorize_credentials
                with _OPERATION_LOCK:
                    result = authorize_credentials(self.config)
                self._send_json(dict(ok=True, **result))
                return

            if path in {"/api/service/start", "/api/service/start_send", "/api/service/restart"}:
                with _OPERATION_LOCK:
                    if "expected_group" in body and body["expected_group"] != self.config.get("group"):
                        raise ValueError("管理群已变更，请重新核对当前群及待发送记录")
                    current = read_status(self.config)
                    send_mode = path.endswith("start_send")
                    if path.endswith("restart"):
                        send_mode = body.get("send", bool(current.get("send_enabled")))
                    if type(send_mode) is not bool:
                        raise ValueError("send 必须为布尔值")
                    restart = path.endswith("restart") or (
                        current.get("running") and bool(current.get("send_enabled")) != send_mode)
                    if restart:
                        self._pause_service_startup(self.config, self.config_path)
                        stop_service(self.config)
                    if current.get("running") and not restart:
                        result = current
                    else:
                        result = start_service(self.config_path, self.config, send=send_mode)
                return self._send_json({"ok": True, "result": _sanitize_service(result)})

            if path == "/api/service/stop":
                with _OPERATION_LOCK:
                    self._pause_service_startup(self.config, self.config_path)
                    result = stop_service(self.config)
                return self._send_json({"ok": True, "result": _sanitize_service(result)})

            if path == "/api/services/reset":
                if body.get("confirm_group") != "邮件测试":
                    raise ValueError("必须确认清空邮件测试数据")
                target_dir = str(Path(body.get("data_dir", "")).resolve())
                with _OPERATION_LOCK:
                    target = next((svc for svc in _list_services(self.config, self.config_path)
                                   if svc.get("data_dir") == target_dir), None)
                    if not target or target.get("group") != "邮件测试":
                        raise PermissionError("仅允许清空已识别的邮件测试服务")
                    result = reset_test_data({"data_dir": target_dir}, instance=body.get("instance"))
                return self._send_json(result)

            if path == "/api/services/stop":
                target = body.get("data_dir")
                if not isinstance(target, str) or not target:
                    raise ValueError("必须指定待停止服务的 data_dir")
                target = str(Path(target).resolve())
                with _OPERATION_LOCK:
                    candidates = _list_services(self.config, self.config_path)
                    record = next((item for item in candidates if item.get("data_dir") == target), None)
                    if record is None:
                        raise PermissionError("未经授权或无法验证的监控服务")
                    config_path = record.get("config_path")
                    config = load_config(config_path) if config_path else {"data_dir": target}
                    self._pause_service_startup(config, config_path)
                    result = stop_service(config)
                return self._send_json({"ok": True, "result": _sanitize_service(result), "stopped_dir": target})

            if path == "/api/group/switch":
                target_group = body.get("group")
                if (not isinstance(target_group, str) or not target_group.strip()
                        or len(target_group) > 200 or any(ord(ch) < 32 for ch in target_group)):
                    raise ValueError("请输入完整、有效的微信群名")
                target_group = target_group.strip()
                with _OPERATION_LOCK:
                    old_config = self.config
                    probe_result = probe(dict(old_config, group=target_group))
                    if not probe_result.get("ok"):
                        return self._send_json({"ok": False, "error":
                            "目标群独立窗口尚未就绪，请先打开该群并保持非最小化；已保留原群配置。",
                            "probe": probe_result})
                    old_status = read_status(old_config)
                    startup = self._pause_service_startup(old_config, self.config_path)
                    stop_service(old_config)
                    old_group = old_config["group"]
                    new_data_dir = resolve_group_data_dir(
                        Path(old_config["data_dir"]).parent, target_group,
                        current_group=old_group, current_data_dir=old_config["data_dir"])
                    with Path(self.config_path).open(encoding="utf-8", newline="") as handle:
                        old_content = handle.read()
                    try:
                        update_toml_root_keys(self.config_path, {
                            "group": target_group, "data_dir": str(new_data_dir)})
                        new_config = load_config(self.config_path)
                        # Old hash-derived agents must not restart with this
                        # config's new group on the next login.
                        if target_group != old_group and startup.get("installed"):
                            from ..autostart import manage_startup
                            manage_startup(self.config_path, old_config, "uninstall")
                        from ..service import atomic_json
                        atomic_json(Path(old_config["data_dir"]) / "group.json", {"group": old_group})
                        atomic_json(new_data_dir / "group.json", {"group": target_group})
                        if startup.get("installed") and sys.platform == "darwin":
                            from ..autostart import manage_startup
                            # Preserve login supervision while leaving the new
                            # group stopped until the user starts preview.
                            manage_startup(self.config_path, new_config, "configure", send=False)
                        self.server.config = new_config
                    except Exception:
                        atomic_write_config(self.config_path, old_content)
                        self.server.config = old_config
                        if startup.get("installed"):
                            from ..autostart import manage_startup
                            was_running = bool(old_status.get("running"))
                            recovery_action = "configure" if sys.platform == "darwin" and not was_running else "install"
                            manage_startup(self.config_path, old_config, recovery_action,
                                           send=bool(old_status.get("send_enabled")))
                            if not was_running and recovery_action == "install":
                                self._pause_service_startup(old_config, self.config_path)
                                stop_service(old_config)
                        elif old_status.get("running"):
                            start_service(self.config_path, old_config, send=bool(old_status.get("send_enabled")))
                        raise
                return self._send_json({"ok": True, "group": target_group,
                    "data_dir": str(new_data_dir), "previous_group": old_group,
                    "message": "群名已保存，数据已切换。点击启动监控（仅预览）开始采集。"})

            if path == "/api/tick":
                with _OPERATION_LOCK:
                    store = Store(Path(self.config["data_dir"]) / "tracker.sqlite3")
                    try:
                        previews = Engine(store, self.config).dispatch(send=False)
                        return self._send_json({"ok": True, "dispatched": len(previews), "previews": previews})
                    finally:
                        store.close()

            if path == "/api/demo/run":
                with _OPERATION_LOCK:
                    demo_db = Path(self.config["data_dir"]) / "demo.sqlite3"
                    store = Store(demo_db)
                    try:
                        records = [json.loads(line) for line in
                            (RESOURCE_ROOT / "screenshot.jsonl").read_text(encoding="utf-8-sig").splitlines()
                            if line.strip()]
                        outcomes = []
                        previews = []
                        for message in records:
                            # Demonstration keeps the fixture's stated source
                            # and date; it is never attributed to the live group.
                            demo_config = dict(self.config, group=message["group"], reminder_minutes=[],
                                               processing_mode="rules")
                            demo_config.pop("config_path", None)
                            engine = Engine(store, demo_config)
                            now = datetime.fromisoformat(message["sent_at"].replace("Z", "+00:00"))
                            outcomes.append(engine.ingest(message, now=now))
                            previews.extend(engine.dispatch(now=now, send=False))
                        return self._send_json({"ok": True, "db": str(demo_db.resolve()),
                            "ingested": len(outcomes), "events": len(store.events()), "previews": len(previews)})
                    finally:
                        store.close()
            return self._send_error("未知控制接口", 404)
        except PermissionError as error:
            self._send_error(str(error), 403)
        except (ValueError, FileNotFoundError) as error:
            self._send_error(str(error), 400 if isinstance(error, ValueError) else 404)
        except Exception as error:
            self._send_error(str(error), 500)
