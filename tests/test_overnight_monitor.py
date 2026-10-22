from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


monitor = load_script("night_monitor_tests", "overnight-monitor.py")
mailer = load_script("night_mailer_tests", "overnight-mailer.py")
MANIFEST = dict(group="<主群>", created_at="2026-10-17T02:40:00+08:00",
                deadline="2026-10-17T08:00:00+08:00", sample_seconds=60)


def report():
    return dict(report_created_at="2026-10-17T08:05:20+08:00", fresh_capture_samples=0,
                samples=10, deep_probes=2, deep_probe_successes=0, sampling_gaps=[], sleep_events=[],
                current_service=dict(running=True, status="monitoring", last_error=None,
                                     last_capture_at="2026-10-17T08:05:19+08:00", snapshots=2),
                power_now=dict(lid=dict(AppleClamshellState=True), power_source="AC Power", sleep_disabled=1),
                observer=dict(state="completed"))


class OvernightObservationTests(unittest.TestCase):
    def test_power_snapshot_keeps_electron_alias_as_a_sleep_prevention_owner(self):
        outputs = {
            ("/usr/bin/pmset", "-g", "assertions"): (
                'PreventUserIdleSystemSleep 1\n'
                'pid 1187(ChatGPT): [0x001] 04:00:00 NoIdleSleepAssertion named: "Electron"\n'
                'pid 2162(UURemote): [0x002] 04:00:00 PreventUserIdleSystemSleep named: "UU"\n'),
            ("/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"): '"AppleClamshellState" = Yes',
            ("/usr/bin/pmset", "-g", "batt"): "Now drawing from 'AC Power'\n100%; charged",
            ("/usr/bin/pmset", "-g", "live"): "System-wide power settings:\n SleepDisabled 1\n",
        }
        with patch.object(monitor, "command", side_effect=lambda args: outputs[tuple(args)]):
            power = monitor.power_state()
        self.assertEqual({owner["process"] for owner in power["sleep_prevention_owners"]}, {"ChatGPT", "UURemote"})
        self.assertEqual(power["sleep_disabled"], 1)

    def test_report_preserves_uu_condition_and_discloses_assistant_sleep_assertion(self):
        manifest = dict(MANIFEST, test_condition="UU 远程防休眠：始终")
        current = report()
        current["power_now"]["sleep_prevention_owners"] = [dict(process="ChatGPT", assertion="NoIdleSleepAssertion")]
        _, body, _ = mailer.render(manifest, current, 1, "2026-10-17T08:05:00+08:00")
        self.assertIn("UU 远程防休眠：始终", body)
        self.assertIn("不能判定已排除它们的保活影响", body)

    def test_health_summary_distinguishes_process_uptime_from_actual_fresh_capture(self):
        rows = [
            dict(sampled_at="2026-10-16T19:00:00+00:00", service=dict(running=True, status="waiting_for_weixin", last_error="target_window_not_ready")),
            dict(sampled_at="2026-10-16T19:01:00+00:00", service=dict(running=True, status="monitoring", last_capture_at="2026-10-16T19:00:59+00:00")),
            dict(sampled_at="2026-10-16T19:12:00+00:00", service=dict(running=True, status="monitoring", last_capture_at="2026-10-16T19:01:00+00:00")),
        ]
        result = monitor.summarize(rows, MANIFEST)
        self.assertEqual(result["service_running_samples"], 3)
        self.assertEqual(result["fresh_capture_samples"], 1)
        self.assertEqual(result["fresh_capture_sample_percent"], 33.33)
        self.assertEqual(result["sampling_gaps"][0]["seconds"], 660)

    def test_recorder_deadline_leaves_the_independent_service_running(self):
        manifest = dict(MANIFEST, deadline="2000-01-01T00:00:00+00:00")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(monitor, "read_status", return_value={"running": True}), \
                    patch.object(monitor.signal, "signal"), \
                    patch.object(monitor, "stop_service", create=True) as stop:
                monitor.run(path, manifest, {})
            stop.assert_not_called()
            final = json.loads((path / "observer.json").read_text())
            self.assertEqual(final["state"], "completed")
            self.assertTrue(final["service_left_running"])

    def test_sleep_parser_does_not_mistake_scheduled_wake_requests_for_actual_wakes(self):
        text = ("2026-10-17 02:49:38 +0800 Sleep              Entering Sleep state due to 'Clamshell Sleep'\n"
                "2026-10-17 02:49:39 +0800 Wake Requests      [process=something]\n"
                "2026-10-17 03:00:50 +0800 Wake               Wake from Deep Idle [X]\n")
        with patch.object(mailer.subprocess, "run") as run:
            run.return_value.stdout = text
            result = mailer.sleep_events("2026-10-17T02:40:00+08:00", "2026-10-17T03:05:00+08:00")
        self.assertEqual([event["event"] for event in result], ["Sleep", "Wake"])
        self.assertEqual(result[0]["reason"], "Clamshell Sleep")

    def test_morning_report_uses_current_autonomous_capture_and_escapes_html(self):
        subject, body, html = mailer.render(MANIFEST, report(), 1, "2026-10-17T08:05:00+08:00")
        self.assertIn("已观察到成功采集", subject)
        self.assertIn("<主群>", body)
        self.assertIn("&lt;主群&gt;", html)
        self.assertNotIn("<主群>", html)
        stale = report()
        stale["current_service"]["last_capture_at"] = "2026-10-17T07:59:00+08:00"
        subject, _, _ = mailer.render(MANIFEST, stale, 1, "2026-10-17T08:05:00+08:00")
        self.assertIn("尚未观察到成功采集", subject)

    def test_each_mail_milestone_sends_at_most_once(self):
        due = "2026-10-17T08:05:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(mailer, "snapshot", return_value=({"email": {}}, report())), \
                    patch.object(mailer, "apply_credentials", return_value=True), \
                    patch.object(mailer, "send_email") as send:
                first = mailer.send_once(Path(directory), MANIFEST, 1, due)
                second = mailer.send_once(Path(directory), MANIFEST, 1, due)
            send.assert_called_once()
            self.assertEqual(first["status"], "smtp_accepted")
            self.assertEqual(second["status"], "smtp_accepted")

    def test_mail_failure_retains_only_error_type_and_is_not_silently_resent(self):
        due = "2026-10-17T08:05:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(mailer, "snapshot", return_value=({"email": {}}, report())), \
                    patch.object(mailer, "apply_credentials", return_value=True), \
                    patch.object(mailer, "send_email", side_effect=RuntimeError("sensitive-test-marker")) as send:
                result = mailer.send_once(Path(directory), MANIFEST, 1, due)
                mailer.send_once(Path(directory), MANIFEST, 1, due)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_type"], "RuntimeError")
            self.assertNotIn("sensitive-test-marker", (Path(directory) / "mail-journal.json").read_text())
            send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
