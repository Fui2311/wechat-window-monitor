import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("observe_monitor", Path(__file__).resolve().parent.parent / "scripts/observe-monitor.py")
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)


class SoakEvidenceTests(unittest.TestCase):
    def test_gap_between_samples_cannot_be_reported_as_continuous_observation(self):
        sequence = 0
        def sample(*args):
            nonlocal sequence
            sequence += 1
            return dict(at="test", issues=[], instance="same", capture_gaps=3 if sequence == 1 else 4,
                        tree_mib=100, capture_age_seconds=1), {}
        psutil = SimpleNamespace(Process=lambda: SimpleNamespace(create_time=lambda: 123))
        with tempfile.TemporaryDirectory() as directory, patch.object(OBSERVER, "read_sample", side_effect=sample):
            result = OBSERVER.observe({}, Path(directory), 0.1, 0.01, psutil)
            self.assertGreaterEqual(result["samples"], 2)
            self.assertFalse(result["passed_monitoring_observation"])
            self.assertEqual(result["capture_gap_increase"], 1)
            self.assertEqual(json.loads((Path(directory) / "summary.json").read_text())["status"], "completed")

    def test_failed_capture_and_process_change_are_preserved_in_evidence(self):
        count = 0
        def sample(*args):
            nonlocal count
            count += 1
            return dict(at="test", issues=["capture_not_ready"], instance="one" if count == 1 else "two",
                        capture_gaps=0, tree_mib=100), {}
        psutil = SimpleNamespace(Process=lambda: SimpleNamespace(create_time=lambda: 123))
        with tempfile.TemporaryDirectory() as directory, patch.object(OBSERVER, "read_sample", side_effect=sample):
            result = OBSERVER.observe({}, Path(directory), 0.1, 0.01, psutil)
            self.assertFalse(result["passed_monitoring_observation"])
            self.assertEqual(result["service_instance_changes"], 1)
            self.assertEqual(result["unhealthy_samples"], result["samples"])


if __name__ == "__main__":
    unittest.main()
