from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from wechat_tracker.autostart import _mac_bootout, _run, mac_agent, windows_task


class Result:
    def __init__(self, code=0, output=b"", error=b""):
        self.returncode, self.stdout, self.stderr = code, output, error


class StartupTests(unittest.TestCase):
    def test_mac_bootout_accepts_an_already_removed_job(self):
        with patch("wechat_tracker.autostart._run", side_effect=[
            Result(3, error=b"Boot-out failed: 3: No such process"), Result(113)
        ]):
            _mac_bootout("gui/501/example")

    def test_mac_bootout_waits_until_the_job_is_removed(self):
        with patch("wechat_tracker.autostart._run", side_effect=[Result(), Result(), Result(113)]), \
                patch("wechat_tracker.autostart.time.sleep") as sleep:
            _mac_bootout("gui/501/example")
        sleep.assert_called_once()

    def test_mac_bootout_waits_for_an_operation_already_in_progress(self):
        with patch("wechat_tracker.autostart._run", side_effect=[Result(36), Result(), Result(113)]), \
                patch("wechat_tracker.autostart.time.sleep") as sleep:
            _mac_bootout("gui/501/example")
        sleep.assert_called_once()

    def test_mac_bootout_does_not_ignore_an_error_for_a_loaded_job(self):
        with patch("wechat_tracker.autostart._run", side_effect=[
            Result(5, error=b"Operation not permitted"), Result()
        ]):
            with self.assertRaisesRegex(RuntimeError, "launchctl bootout.*Operation not permitted"):
                _mac_bootout("gui/501/example")

    def test_mac_bootout_does_not_claim_success_if_the_job_remains_loaded(self):
        with patch("wechat_tracker.autostart._run", return_value=Result()):
            with self.assertRaisesRegex(RuntimeError, "did not finish"):
                _mac_bootout("gui/501/example", timeout=0)

    def test_startup_errors_identify_the_actual_command(self):
        with patch("wechat_tracker.autostart.subprocess.run", return_value=Result(3, error=b"No such process")):
            with self.assertRaisesRegex(RuntimeError, "launchctl bootout.*exit 3.*No such process"):
                _run(["/bin/launchctl", "bootout", "gui/501/example"])

    def test_mac_status_query_failure_is_not_treated_as_an_absent_job(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("wechat_tracker.autostart.Path.home", return_value=Path(directory)), \
                patch("wechat_tracker.autostart._run", return_value=Result(5, error=b"Operation not permitted")):
            with self.assertRaisesRegex(RuntimeError, "launchctl print.*Operation not permitted"):
                mac_agent("sample.toml", dict(group="target"), "pause")

    def test_mac_pause_does_not_claim_an_absent_startup_is_installed(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("wechat_tracker.autostart.Path.home", return_value=Path(directory)), \
                patch("wechat_tracker.autostart._run", return_value=Result(113)):
            result = mac_agent("sample.toml", dict(group="target"), "pause")
            self.assertFalse(result["installed"])

    def test_windows_independent_timer_recovers_crash_and_stop_can_disable_it(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(group="target", data_dir=directory)
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if "/Query" in args:
                    return Result(1)
                if args[0] == "whoami.exe":
                    return Result(output=b'"user","S-1-5-21-123"\r\n')
                return Result()
            with patch("wechat_tracker.autostart._run", side_effect=run):
                windows_task("sample.toml", config, "install", send=True)
            root = ET.parse(Path(directory) / "startup-task.xml").getroot()
            self.assertEqual(root.find("{*}Triggers/{*}TimeTrigger/{*}Repetition/{*}Interval").text, "PT1M")
            self.assertEqual(root.find("{*}Settings/{*}MultipleInstancesPolicy").text, "IgnoreNew")
            self.assertEqual(root.find("{*}Settings/{*}ExecutionTimeLimit").text, "PT0S")
            self.assertIn("--send", root.find("{*}Actions/{*}Exec/{*}Arguments").text)
            self.assertEqual(root.find("{*}Principals/{*}Principal/{*}LogonType").text, "InteractiveToken")
            with patch("wechat_tracker.autostart._run", return_value=Result(output=ET.tostring(root))) as mocked:
                windows_task("sample.toml", config, "pause")
                self.assertIn("/DISABLE", mocked.call_args.args[0])
                windows_task("sample.toml", config, "start")
                self.assertTrue(any("/ENABLE" in call.args[0] for call in mocked.call_args_list))

    def test_foreign_windows_task_is_never_changed(self):
        with patch("wechat_tracker.autostart._run", return_value=Result(output=b"<Task/>")) as mocked:
            with self.assertRaisesRegex(RuntimeError, "not owned"):
                windows_task("sample.toml", dict(group="target"), "install")
            self.assertEqual(mocked.call_count, 1)

    def test_mac_agent_uses_native_runtime_and_preserves_config_ownership(self):
        with tempfile.TemporaryDirectory() as directory, patch("wechat_tracker.autostart.Path.home", return_value=Path(directory)), patch("wechat_tracker.autostart.os.getuid", return_value=501, create=True), patch("wechat_tracker.autostart._run", side_effect=lambda args, **kwargs: Result(113 if args[1] == "print" else 0)):
            result = mac_agent("sample.toml", dict(group="target"), "install", send=True)
            payload = plistlib.loads((Path(directory) / "Library/LaunchAgents" / (result["label"] + ".plist")).read_bytes())
            self.assertTrue(payload["RunAtLoad"])
            self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
            self.assertIn("--send", payload["ProgramArguments"])
            with self.assertRaisesRegex(RuntimeError, "another configuration"):
                mac_agent("different.toml", dict(group="target"), "install")

    def test_mac_configure_stays_stopped_and_start_updates_send_mode(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("wechat_tracker.autostart.Path.home", return_value=Path(directory)), \
                patch("wechat_tracker.autostart._run", side_effect=lambda args, **kwargs: Result(113 if args[1] == "print" else 0)) as run:
            config = dict(group="target")
            result = mac_agent("sample.toml", config, "configure")
            target = Path(directory) / "Library/LaunchAgents" / (result["label"] + ".plist")
            self.assertTrue(target.exists())
            self.assertFalse(any("bootstrap" in call.args[0] for call in run.call_args_list))
            for send in (True, False):
                mac_agent("sample.toml", config, "start", send=send)
                payload = plistlib.loads(target.read_bytes())
                self.assertEqual("--send" in payload["ProgramArguments"], send)


if __name__ == "__main__":
    unittest.main()
