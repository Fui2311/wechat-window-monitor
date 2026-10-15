"""Long-running, supervised monitoring with durable health and bounded logs."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
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
    if not value.get("running"):
        return value
    with socket.create_connection(("127.0.0.1", value["control_port"]), timeout=2) as connection:
        connection.sendall((json.dumps({"action": "stop", "token": value["control_token"]}) + "\n").encode())
        connection.recv(1024)
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
        self.health = dict(instance=uuid.uuid4().hex, running=True, status="starting", pid=os.getpid(),
                           started_at=timestamp(), heartbeat_at=timestamp(), group=config["group"],
                           send_enabled=send, worker_pid=None, worker_restarts=0, snapshots=0,
                           observed_messages=0, events_created=0, email_sent=0, email_failures=0,
                           last_capture_at=None, last_error=None, capture_gaps=0,
                           sqlite_version=sqlite3.sqlite_version, journal_mode="delete", backup_at=None,
                           coverage="loaded target-window viewport; complete history is not guaranteed")
        self.log = logging.getLogger("wechat.service." + self.health["instance"])
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        handler = RotatingFileHandler(self.directory / "service.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.log.addHandler(handler)

    def _save(self):
        with self.guard:
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
                    else:
                        response = {"error": "unsupported_action"}
                    connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                except (ValueError, OSError):
                    pass

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
        if messages:
            messages, forwards = enrich_forwards(messages, self.store, self.config,
                                                 include_existing=self.settings.get("include_existing", True))
            outcomes = engine.capture_snapshot(messages, self.settings.get("include_existing", True))
            self.health["snapshots"] += int(kind == "snapshot")
            self.health["observed_messages"] = len(messages)
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
                if self.config.get("capture", {}).get("source") == "jsonl":
                    if now >= next_worker:
                        messages = capture_once(self.config)
                        outcomes = engine.capture_snapshot(messages, self.settings.get("include_existing", True))
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
                if now >= next_backup:
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
