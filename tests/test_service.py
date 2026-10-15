from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from wechat_tracker.capture import CaptureError
from wechat_tracker.service import InstanceLock, MonitorService, read_status, stop_service
from wechat_tracker.store import Store
from wechat_tracker.stream import NativeStream

GROUP = "2026级计算机研究生通知群"


def config(directory):
    return dict(group=GROUP, data_dir=str(directory), reminder_minutes=[1440, 60], email={},
                capture=dict(source="windows_uia", auto_expand_forward=False, follow_tail=False),
                service=dict(resync_seconds=2, dispatch_seconds=1, prevent_sleep=False))


def snapshot():
    text = json.loads((Path(__file__).resolve().parent.parent / "examples/screenshot.jsonl").read_text(encoding="utf-8"))["text"]
    return dict(kind="snapshot", event_bound=True, capture_ms=5,
                result=dict(ok=True, status="ready", group=GROUP, observed_at="2026-10-15T06:00:00+00:00",
                            diagnostics={}, messages=[dict(text=text, class_name="mmui::ChatTextItemView", runtime_id=[1, 2])]))


class FakeStream:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False
        self.pid = 123456

    def start(self):
        pass

    def next_frame(self, timeout=0.5):
        if self.frames:
            frame = self.frames.pop(0)
            if isinstance(frame, Exception):
                raise frame
            return frame
        time.sleep(0.01)
        return None

    def close(self):
        self.closed = True


class ServiceTests(unittest.TestCase):
    def test_corrupt_database_reports_failure_and_releases_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "tracker.sqlite3").write_bytes(b"broken database" * 100)
            service = MonitorService(config(directory), stream_factory=lambda _: self.fail("must not start capture"))
            with self.assertRaisesRegex(RuntimeError, "Database startup"):
                service.run(run_seconds=0.1)
            self.assertEqual(read_status(config(directory))["status"], "database_error")
            lock = InstanceLock(directory)
            lock.acquire()
            lock.release()

    def test_os_lock_excludes_second_instance_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            one, two = InstanceLock(directory), InstanceLock(directory)
            one.acquire()
            with self.assertRaises(RuntimeError):
                two.acquire()
            one.release()
            two.acquire()
            two.release()

    def test_native_snapshot_to_persistent_events_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = FakeStream([snapshot(), snapshot()])
            service = MonitorService(config(directory), stream_factory=lambda _: stream)
            with patch("wechat_tracker.engine.send_email", side_effect=AssertionError("SMTP is disabled")):
                health = service.run(run_seconds=0.1)
            self.assertTrue(stream.closed)
            self.assertFalse(health["running"])
            self.assertEqual(health["events_created"], 1)
            with StoreContext(Path(directory) / "tracker.sqlite3") as store:
                self.assertEqual(len(store.events()), 1)
                self.assertEqual(len(store.jobs()), 3)

    def test_capture_gap_is_recorded_and_closed_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            failed = dict(kind="state", result=dict(ok=False, group=GROUP, status="window_not_found"))
            stream = FakeStream([snapshot(), failed, snapshot()])
            health = MonitorService(config(directory), stream_factory=lambda _: stream).run(run_seconds=0.1)
            self.assertEqual(health["capture_gaps"], 1)
            with StoreContext(Path(directory) / "tracker.sqlite3") as store:
                gaps = store.db.execute("SELECT * FROM capture_gaps").fetchall()
                self.assertEqual(len(gaps), 1)
                self.assertIsNotNone(gaps[0]["ended_at"])
                self.assertEqual(len(store.events()), 1)

    def test_worker_exit_is_observed_and_recovery_scheduled(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = FakeStream([CaptureError("exited")])
            health = MonitorService(config(directory), stream_factory=lambda _: stream).run(run_seconds=0.1)
            self.assertTrue(stream.closed)
            self.assertEqual(health["worker_restarts"], 1)
            self.assertIsNone(health["worker_pid"])

    def test_foreign_group_frame_never_enters_database(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = snapshot()
            frame["result"]["group"] = "another group"
            stream = FakeStream([frame])
            health = MonitorService(config(directory), stream_factory=lambda _: stream).run(run_seconds=0.1)
            self.assertEqual(health["worker_restarts"], 1)
            with StoreContext(Path(directory) / "tracker.sqlite3") as store:
                self.assertEqual(store.events(), [])

    def test_forward_retry_runs_during_unchanged_worker_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            frame = snapshot()
            heartbeat = dict(kind="heartbeat", group=GROUP, observed_at=frame["result"]["observed_at"], diagnostics={})
            stream = FakeStream([frame, heartbeat])
            with patch("wechat_tracker.service.enrich_forwards", side_effect=lambda messages, *a, **k: (messages, [])) as enrich:
                MonitorService(config(directory), stream_factory=lambda _: stream).run(run_seconds=0.1)
            self.assertEqual(enrich.call_count, 2)

    def test_stop_handshake_waits_for_os_lock_release(self):
        with tempfile.TemporaryDirectory() as directory:
            options = config(directory)
            stream = FakeStream([snapshot()])
            service = MonitorService(options, stream_factory=lambda _: stream)
            thread = threading.Thread(target=service.run)
            thread.start()
            try:
                deadline = time.monotonic() + 3
                while not read_status(options).get("running") and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(read_status(options)["running"])
                stopped = stop_service(options)
                self.assertFalse(stopped["running"])
                lock = InstanceLock(directory)
                lock.acquire()
                lock.release()
            finally:
                service.stop.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_clean_stop_status_does_not_claim_unresponsive(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MonitorService(config(directory), stream_factory=lambda _: FakeStream([]))
            service.run(run_seconds=0.02)
            self.assertEqual(read_status(config(directory))["status"], "stopped")

    def test_missing_smtp_keeps_jobs_pending_without_consuming_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = FakeStream([snapshot()])
            service = MonitorService(config(directory), send=True, stream_factory=lambda _: stream)
            with patch("wechat_tracker.credentials.apply_credentials", return_value=False), patch("wechat_tracker.engine.send_email") as send:
                health = service.run(run_seconds=0.1)
            send.assert_not_called()
            self.assertFalse(health["send_ready"])
            with StoreContext(Path(directory) / "tracker.sqlite3") as store:
                self.assertTrue(all(job["attempts"] == 0 and job["status"] == "pending" for job in store.jobs()))

    def test_restart_persists_deduplication_and_gap_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                MonitorService(config(directory), stream_factory=lambda _: FakeStream([snapshot()])).run(run_seconds=0.05)
            with StoreContext(Path(directory) / "tracker.sqlite3") as store:
                self.assertEqual(len(store.events()), 1)
                self.assertEqual(len(store.jobs()), 3)
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM capture_gaps WHERE reason='service_restart' AND ended_at IS NOT NULL").fetchone()[0], 1)

    def test_stale_file_is_not_proof_of_running_process(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "service.json").write_text(json.dumps(dict(running=True, pid=123, control_port=1, control_token="x", instance="x")), encoding="utf-8")
            self.assertFalse(read_status(config(directory))["running"])

    def test_native_transport_reads_real_child_and_cleans_up(self):
        stream = NativeStream({})
        with patch.object(stream, "command", return_value=[sys.executable, "-c", "import json,time; print(json.dumps({'kind':'heartbeat','group':'test'}),flush=True); time.sleep(30)"]):
            try:
                stream.start()
                frame = stream.next_frame(timeout=5)
                self.assertEqual(frame["kind"], "heartbeat")
            finally:
                stream.close()
        self.assertIsNotNone(stream.process.poll())

    def test_native_transport_rejects_non_json_frame(self):
        stream = NativeStream({})
        with patch.object(stream, "command", return_value=[sys.executable, "-c", "print('not-json')"]):
            try:
                stream.start()
                with self.assertRaises(CaptureError):
                    stream.next_frame(timeout=5)
            finally:
                stream.close()


class StoreContext:
    def __init__(self, path):
        self.store = Store(path)

    def __enter__(self):
        return self.store

    def __exit__(self, *args):
        self.store.close()


if __name__ == "__main__":
    unittest.main()
