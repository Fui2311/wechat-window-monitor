from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from wechat_tracker.autostart import mac_agent, windows_task


class Result:
    def __init__(self, code=0, output=b""):
        self.returncode, self.stdout = code, output


class StartupTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as directory, patch("wechat_tracker.autostart.Path.home", return_value=Path(directory)), patch("wechat_tracker.autostart.os.getuid", return_value=501, create=True), patch("wechat_tracker.autostart._run", return_value=Result(1)):
            result = mac_agent("sample.toml", dict(group="target"), "install", send=True)
            payload = plistlib.loads((Path(directory) / "Library/LaunchAgents" / (result["label"] + ".plist")).read_bytes())
            self.assertTrue(payload["RunAtLoad"])
            self.assertEqual(payload["KeepAlive"], {"SuccessfulExit": False})
            self.assertIn("--send", payload["ProgramArguments"])
            with self.assertRaisesRegex(RuntimeError, "another configuration"):
                mac_agent("different.toml", dict(group="target"), "install")


if __name__ == "__main__":
    unittest.main()
