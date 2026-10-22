"""Long-running, supervised monitoring with durable health and bounded logs."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from .capture import CaptureError, capture_once, normalize_uia_snapshot
from .engine import Engine
from .store import Store
from .stream import NativeStream
from .watcher import enrich_forwards


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


class InstanceLock:
    """OS-owned byte lock survives stale files and is released when a process dies."""
    def __init__(self, directory):
        self.path = Path(directory) / "service.lock"
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            self.handle.seek(0, 2)
            if not self.handle.tell():
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Monitoring is already running for this data directory") from None

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def read_status(config):
    path = Path(config["data_dir"]) / "service.json"
    if not path.exists():
        return {"running": False, "status": "not_started"}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("running") is False:
        return value
    # A live control socket proves identity, not merely existence of a reused PID.
    try:
        with socket.create_connection(("127.0.0.1", value["control_port"]), timeout=1) as connection:
            connection.sendall((json.dumps({"action": "status", "token": value["control_token"]}) + "\n").encode())
            response = connection.makefile("rb").readline(65536)
            current = json.loads(response)
            if current.get("instance") != value.get("instance"):
                raise ValueError("Instance mismatch")
            return current
    except (OSError, ValueError, KeyError):
        return dict(value, running=False, status="stopped_or_unresponsive")


def stop_service(config, timeout=15):
    value = read_status(config)
    if value.get("running"):
        try:
            with socket.create_connection(("127.0.0.1", value["control_port"]), timeout=2) as connection:
                connection.sendall((json.dumps({"action": "stop", "token": value["control_token"]}) + "\n").encode())
                connection.recv(1024)
        except OSError:
            # launchctl bootout can stop the process between status and this
            # request. The OS lock below proves shutdown even if the socket reset.
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lock = InstanceLock(config["data_dir"])
        try:
            lock.acquire()
        except RuntimeError:
            pass
        else:
            lock.release()
            return read_status(config)
        time.sleep(0.2)
    raise RuntimeError("Service did not stop within its shutdown deadline; no PID was killed blindly")


def reset_test_data(config, *, instance):
    """Ask the owning monitor to reset on its SQLite thread, retaining credentials."""
    status = read_status(config)
    if status.get("group") != "邮件测试" or not status.get("running") or not status.get("reset_supported"):
        raise ValueError("仅支持清空运行中的“邮件测试”服务，请先启动或更新该服务")
    if status.get("instance") != instance:
        raise ValueError("服务已发生变化，请刷新服务列表后重试")
    with socket.create_connection(("127.0.0.1", status["control_port"]), timeout=2) as connection:
        connection.settimeout(60)
        connection.sendall((json.dumps(dict(action="reset_test_data", token=status["control_token"],
                                            instance=instance, group="邮件测试")) + "\n").encode())
        result = json.loads(connection.makefile("rb").readline(65536))
    if not result.get("ok"):
        raise ValueError(result.get("error", "清空未完成，请刷新状态"))
    return result


def start_service(config_path, config, send=False):
    current = read_status(config)
    if current.get("running"):
        return current
    probe = InstanceLock(config["data_dir"])
    probe.acquire()
    probe.release()
    command = [sys.executable, "-m", "wechat_tracker", "--config", str(Path(config_path).resolve()), "service", "run"]
    if send:
        command.append("--send")
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   cwd=str(Path(__file__).resolve().parent.parent))
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    from .autostart import manage_startup
    supervised = sys.platform in {"win32", "darwin"} and manage_startup(config_path, config, "status").get("installed")
    if supervised:
        manage_startup(config_path, config, "start", send=send)
        child = None
    else:
        child = subprocess.Popen(command, **options)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        current = read_status(config)
        if current.get("running"):
            return current
        if child is not None and child.poll() is not None:
            raise RuntimeError("Service startup failed; inspect state/service.log")
        time.sleep(0.2)
    raise RuntimeError("Service startup handshake did not complete; inspect state/service.log")


class MonitorService:
    def __init__(self, config, *, send=False, stream_factory=NativeStream):
        self.config, self.send = config, send
        self.directory = Path(config["data_dir"])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings = config.get("service", {})
        self.stream_factory = stream_factory
        self.stop = threading.Event()
        self.lock = InstanceLock(self.directory)
        self.store = None
        self.worker = None
        self.server = None
        self.sleep_guard = None
        self.latest_messages = []
        self.guard = threading.RLock()
        self.reset_requests = queue.Queue()
        self.health = dict(instance=uuid.uuid4().hex, running=True, status="starting", pid=os.getpid(),
                           started_at=timestamp(), heartbeat_at=timestamp(), group=config["group"],
                           send_enabled=send, worker_pid=None, worker_restarts=0, snapshots=0,
                           observed_messages=0, events_created=0, email_sent=0, email_failures=0,
                           last_capture_at=None, last_error=None, capture_gaps=0,
                           sqlite_version=sqlite3.sqlite_version, journal_mode="delete", backup_at=None,
                           coverage="loaded target-window viewport; complete history is not guaranteed")
        self.health["config_path"] = config.get("config_path", "")
        self.health["reset_supported"] = config["group"] == "邮件测试"
        self.log = logging.getLogger("wechat.service." + self.health["instance"])
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        handler = RotatingFileHandler(self.directory / "service.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.log.addHandler(handler)

    def _save(self):
        with self.guard:
            from .processing import effective_processing_config
            try:
                effective = effective_processing_config(self.config)
                self.health.update(processing_mode=effective.get("processing_mode", "rules"),
                                   processing_model=effective.get("llm_model", ""),
                                   processing_settings_shared=effective["processing_settings_shared"])
            except (OSError, ValueError):
                self.health["processing_last_error"] = "处理配置读取或校验失败"
            self.health["heartbeat_at"] = timestamp()
            atomic_json(self.directory / "service.json", self.health)

    def _control(self):
        self.server.settimeout(0.5)
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(1)
                try:
                    request = json.loads(connection.makefile("rb").readline(4096))
                    if request.get("token") != self.health["control_token"]:
                        response = {"error": "unauthorized"}
                    elif request.get("action") == "stop":
                        self.stop.set()
                        response = {"stopping": True}
                    elif request.get("action") == "status":
                        with self.guard:
                            response = dict(self.health)
                    elif request.get("action") == "reset_test_data":
                        if (self.config["group"] != "邮件测试" or request.get("group") != "邮件测试"
                                or request.get("instance") != self.health["instance"]):
                            response = {"error": "只能清空当前邮件测试服务"}
                        else:
                            done, response = threading.Event(), {}
                            self.reset_requests.put((done, response))
                            if not done.wait(55):
                                response = {"error": "清理仍在等待监控响应，请刷新状态后核对"}
                    else:
                        response = {"error": "unsupported_action"}
                    connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                except (ValueError, OSError):
                    pass

    def _reset_test_data(self):
        if self.config["group"] != "邮件测试":
            raise ValueError("只能清空邮件测试数据")
        if self.worker:
            self.worker.close()
            self.worker = None
        self.health["worker_pid"] = None
        # Prepare and validate the complete new baseline before touching live data.
        # No card expansion or SMTP occurs during baseline preparation.
        passive = dict(self.config, capture=dict(self.config.get("capture", {}),
                                                follow_tail=False, recover_window=False))
        messages = capture_once(passive)
        baseline = Store(":memory:")
        try:
            messages, _ = enrich_forwards(messages, baseline, self.config, include_existing=False)
            Engine(baseline, self.config).capture_snapshot(messages, include_existing=False)
            tables = ("messages", "events", "outbox", "capture_seen", "capture_state", "forward_reads", "capture_gaps")
            counts = {table: self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in tables}
            with self.store.db:
                for table in tables:
                    self.store.db.execute(f"DELETE FROM {table}")
                for table in ("capture_seen", "capture_state", "forward_reads"):
                    for row in baseline.db.execute(f"SELECT * FROM {table}"):
                        marks = ",".join("?" for _ in row)
                        self.store.db.execute(f"INSERT INTO {table} VALUES ({marks})", tuple(row))
        finally:
            baseline.close()
        self.settings["include_existing"] = False
        self.latest_messages = messages
        self.health.update(reset_at=timestamp(), snapshots=0, observed_messages=len(messages),
                           events_created=0, email_sent=0, email_failures=0, due_previews=0,
                           capture_gaps=0, forward_pending_body=0, forward_status=[], backup_at=None,
                           last_error=None, status="monitoring", last_capture_at=timestamp())
        self._save()
        self.log.info("Test data cleared; baseline=%s; SMTP sending=%s", len(messages), self.send)
        return dict(ok=True, cleared=counts, baseline_count=len(messages), send_enabled=self.send,
                    reset_at=self.health["reset_at"])

    def _process_reset_requests(self):
        processed = False
        while not self.reset_requests.empty():
            done, response = self.reset_requests.get_nowait()
            try:
                response.update(self._reset_test_data())
            except Exception as error:
                response.update(ok=False, error=f"清空失败：{error}")
            finally:
                done.set()
            processed = True
        return processed

    def _gap(self, status):
        if self.health["status"] == "monitoring":
            self.health["capture_gaps"] += 1
            with self.store.db:
                self.store.db.execute("INSERT INTO capture_gaps(started_at,reason) VALUES (?,?)", (timestamp(), status))
        self.health.update(status="waiting_for_weixin", last_error=status)

    def _frame(self, frame, engine):
        kind = frame.get("kind")
        if kind == "state":
            result = frame.get("result", {})
            if result.get("group") != self.config["group"]:
                raise CaptureError("Worker state belongs to another group")
            self._gap(result.get("status", "capture_failed"))
            return
        if kind not in {"snapshot", "heartbeat"}:
            raise CaptureError("Unknown native frame kind")
        result = frame.get("result", {}) if kind == "snapshot" else frame
        if result.get("group") != self.config["group"]:
            raise CaptureError("Worker frame belongs to another group")
        if self.health["status"] != "monitoring":
            with self.store.db:
                self.store.db.execute("UPDATE capture_gaps SET ended_at=? WHERE ended_at IS NULL", (timestamp(),))
            self.log.info("Capture ready; bound exact group window")
        self.health.update(status="monitoring", last_capture_at=result.get("observed_at"), last_error=None,
                           capture_ms=frame.get("capture_ms"), event_bound=frame.get("event_bound", False),
                           diagnostics=result.get("diagnostics", {}))
        self.health["tail_position_verified"] = result.get("diagnostics", {}).get("at_tail") is True
        if kind == "snapshot":
            if self.config.get("capture", {}).get("source") == "macos_ax":
                from .macos import normalize_snapshot
                messages = normalize_snapshot(result, self.config)
            else:
                messages = normalize_uia_snapshot(result, self.config)
            self.latest_messages = messages
        else:
            messages = self.latest_messages
        tail = result.get("diagnostics", {}).get("tail_follow", {})
        capture = self.config.get("capture", {})
        deferred = (capture.get("source") == "macos_ax" and capture.get("recover_window", False)
                    and capture.get("follow_tail", True) and
                    tail.get("action") in {"deferred_recovery", "deferred_user_busy", "deferred_ui_busy", "cooldown"})
        self.health["capture_deferred_reason"] = (tail.get("error") or tail.get("action")) if deferred else None
        if deferred:
            # Retain the snapshot for a later ready heartbeat, but do not turn
            # a user-scrolled historical viewport into new lecture emails.
            return
        if messages:
            messages, forwards = enrich_forwards(messages, self.store, self.config,
                                                 include_existing=self.settings.get("include_existing", True))
            observed_count = len(messages)
            wait_body = (self.config.get("capture", {}).get("auto_expand_forward", False) and
                         self.config.get("capture", {}).get("forward_wait_for_body", False))
            pending = [message for message in messages if wait_body and message.get("kind") == "forward"
                       and message.get("source") in {"macos_ax", "windows_uia"} and not message.get("children")]
            self.health["forward_pending_body"] = len(pending)
            if pending:
                messages = [message for message in messages if message not in pending]
            outcomes = engine.capture_snapshot(messages, self.settings.get("include_existing", True))
            self.health.update(engine.last_processing_report)
            self.health["snapshots"] += int(kind == "snapshot")
            self.health["observed_messages"] = observed_count
            self.health["events_created"] += sum(len(item["events"]) for item in outcomes)
            self.health["forward_status"] = forwards

    def run(self, *, run_seconds=None):
        self.lock.acquire()
        try:
            self.store = Store(self.directory / "tracker.sqlite3")
        except (RuntimeError, sqlite3.DatabaseError):
            self.health.update(running=False, status="database_error", last_error="database_integrity_failed")
            self._save()
            self.lock.release()
            for handler in list(self.log.handlers):
                handler.close()
                self.log.removeHandler(handler)
            raise RuntimeError("Database startup check failed; monitoring is stopped to preserve local state") from None
        previous_file = self.directory / "service.json"
        if previous_file.is_file():
            try:
                previous = json.loads(previous_file.read_text(encoding="utf-8"))
                if previous.get("last_capture_at"):
                    with self.store.db:
                        self.store.db.execute("INSERT INTO capture_gaps(started_at,reason) VALUES (?,?)",
                            (previous["last_capture_at"], "process_restart" if previous.get("running") else "service_restart"))
            except (OSError, ValueError):
                pass
        self.health["capture_gaps"] = self.store.db.execute("SELECT COUNT(*) FROM capture_gaps").fetchone()[0]
        engine = Engine(self.store, self.config)
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.health.update(control_port=self.server.getsockname()[1], control_token=uuid.uuid4().hex)
        self._save()
        threading.Thread(target=self._control, daemon=True, name="monitor-control").start()
        for signum in (signal.SIGINT, signal.SIGTERM):
            if threading.current_thread() is threading.main_thread():
                signal.signal(signum, lambda *_: self.stop.set())
        started = time.monotonic()
        last_frame = started
        next_dispatch = started
        next_save = started
        next_worker = started
        next_backup = started
        failure_status = None
        failures = 0
        self.log.info("Monitoring process started; SMTP sending=%s", self.send)
        if sys.platform == "win32" and self.settings.get("prevent_sleep", True):
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        elif sys.platform == "darwin" and self.settings.get("prevent_sleep", True):
            self.sleep_guard = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while not self.stop.is_set() and (run_seconds is None or time.monotonic() - started < run_seconds):
                now = time.monotonic()
                if self._process_reset_requests():
                    next_worker = now
                    engine = Engine(self.store, self.config)
                if self.config.get("capture", {}).get("source") == "jsonl":
                    if now >= next_worker:
                        messages = capture_once(self.config)
                        outcomes = engine.capture_snapshot(messages, self.settings.get("include_existing", True))
                        self.health.update(engine.last_processing_report)
                        self.health.update(status="monitoring", last_capture_at=timestamp(), observed_messages=len(messages))
                        self.health["snapshots"] += 1
                        self.health["events_created"] += sum(len(item["events"]) for item in outcomes)
                        next_worker = now + self.settings.get("resync_seconds", 15)
                    self.stop.wait(0.2)
                elif now >= next_worker:
                    try:
                        if self.worker is None:
                            self.worker = self.stream_factory(self.config)
                            self.worker.start()
                            self.health["worker_pid"] = self.worker.pid
                            last_frame = now
                        frame = self.worker.next_frame(timeout=0.5)
                        if frame is not None:
                            self._frame(frame, engine)
                            last_frame = now
                            failures = 0
                        elif now - last_frame > self.settings.get("worker_stale_seconds", 90):
                            raise CaptureError("worker_heartbeat_deadline_exceeded")
                    except (CaptureError, OSError) as error:
                        if self.worker:
                            self.worker.close()
                        self.worker = None
                        failures += 1
                        self.health["worker_restarts"] += 1
                        self.health["worker_pid"] = None
                        self._gap(type(error).__name__)
                        self.log.warning("Native worker recovery: %s", type(error).__name__)
                        next_worker = now + min(60, 2 ** min(failures, 6))
                else:
                    self.stop.wait(0.2)
                if now >= next_dispatch:
                    from .credentials import apply_credentials
                    try:
                        ready = apply_credentials(self.config) if self.send else False
                    except (RuntimeError, OSError, ValueError):
                        ready = False
                        self.health["email_state"] = "credential_access_failed"
                    else:
                        self.health["email_state"] = "ready" if ready else "not_configured" if self.send else "preview_only"
                    self.health["send_ready"] = bool(self.send and ready)
                    deliveries = engine.dispatch(send=self.send and ready)
                    self.health["email_sent"] += sum(item["status"] == "sent" for item in deliveries)
                    self.health["email_failures"] += sum(item["status"] not in {"sent", "preview"} for item in deliveries)
                    self.health["due_previews"] = sum(item["status"] == "preview" for item in deliveries)
                    next_dispatch = now + self.settings.get("dispatch_seconds", 5)
                if now >= next_save:
                    self._save()
                    next_save = now + 5
                if self.settings.get("backup_enabled", True) and now >= next_backup:
                    self.store.backup(self.directory / "tracker.last-good.sqlite3")
                    self.health["backup_at"] = timestamp()
                    next_backup = now + 3600
        except sqlite3.DatabaseError:
            failure_status = "database_error"
            self.health["last_error"] = "database_operation_failed"
            self.log.error("Database operation failed; preserve state and inspect the verified backup")
            raise RuntimeError("Database operation failed; monitoring stopped") from None
        finally:
            self.stop.set()
            if self.worker:
                self.worker.close()
            if self.sleep_guard and self.sleep_guard.poll() is None:
                self.sleep_guard.terminate()
                self.sleep_guard.wait(timeout=3)
            self.health.update(running=False, status=failure_status or "stopped", stopped_at=timestamp(), worker_pid=None)
            self._save()
            if self.server:
                self.server.close()
            self.store.close()
            self.lock.release()
            if sys.platform == "win32" and self.settings.get("prevent_sleep", True):
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            self.log.info("Monitoring process stopped cleanly")
            for handler in list(self.log.handlers):
                handler.close()
                self.log.removeHandler(handler)
        return self.health
