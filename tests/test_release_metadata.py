"""Release labels must derive from one source without changing history or local files."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sync_version", ROOT / "scripts" / "sync-version.py")
sync_version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_version)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ["pyproject.toml", *sync_version.FIELDS]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.history = self.root / "CHANGELOG.md"
        self.history.write_text("Historic versions 0.4.0 and 1.0.0 remain intact.\n", encoding="utf-8")
        project = self.root / "pyproject.toml"
        content = project.read_text(encoding="utf-8")
        old = sync_version.tomllib.loads(content)["project"]["version"]
        project.write_text(content.replace('version = "' + old + '"', 'version = "9.8.7"'), encoding="utf-8")

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in sync_version.FIELDS}

    def test_check_is_read_only_and_write_updates_all_derived_fields_only(self):
        before = self.snapshot()
        version, changed = sync_version.synchronize(self.root)
        self.assertEqual(version, "9.8.7")
        self.assertEqual(set(changed), set(sync_version.FIELDS))
        self.assertEqual(self.snapshot(), before)
        sync_version.synchronize(self.root, write=True)
        self.assertEqual(sync_version.synchronize(self.root), ("9.8.7", []))
        self.assertEqual(self.history.read_text(encoding="utf-8"), "Historic versions 0.4.0 and 1.0.0 remain intact.\n")

    def test_invalid_target_prevents_partial_writes(self):
        (self.root / "docs/architecture.md").write_text("missing version field", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Expected exactly one"):
            sync_version.synchronize(self.root, write=True)
        self.assertEqual(self.snapshot(), before)

    def test_checkout_labels_match_the_canonical_version(self):
        _, changed = sync_version.synchronize(ROOT)
        self.assertEqual(changed, [])
