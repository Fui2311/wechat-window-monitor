"""Database discovery boundaries use valid, exclusively temporary fixtures."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from console_fixtures import isolated_console
from wechat_tracker.console import discovery, handler, queries


def database(folder, group):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tracker.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE capture_state (group_name TEXT, last_success_at TEXT)")
            connection.execute("INSERT INTO capture_state VALUES (?,?)", (group, "2026-10-22T00:00:00Z"))
    return path.resolve()


class ConsoleDiscoveryTests(unittest.TestCase):
    def test_valid_backup_and_hidden_trees_are_not_database_sources(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            root = Path(directory) / "state"
            active = database(root / "current", "Current")
            visible = database(root / "history", "History")
            for name in ("backup-20261022", "before-engineering", "previous-run", "last-good", ".hidden"):
                database(root / name / "nested", name)
            found = queries._list_databases(str(active.parent), "Current")
            self.assertEqual({Path(item["path"]) for item in found}, {active, visible})

    def test_backup_named_ancestor_does_not_hide_history_or_explicit_service_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "backup-parent"
            root.mkdir()
            with isolated_console(root):
                active = database(root / "state/current", "Current")
                visible = database(root / "state/history", "History")
                managed = database(root / "state/previous-managed", "Managed")
                found = queries._list_databases(str(active.parent), "Current", services=[
                    dict(data_dir=str(managed.parent), group="Managed", running=True)])
                self.assertEqual({Path(item["path"]) for item in found}, {active, visible, managed})
                self.assertTrue(next(item for item in found if item["path"] == str(managed))["is_live_service"])

    def test_snapshot_databases_are_not_opened_during_automatic_discovery(self):
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            root = Path(directory) / "state"
            active = database(root / "current", "Current")
            snapshot = database(root / "before-change/deep", "Snapshot")
            original = queries._safe_db_connect
            with patch.object(queries, "_safe_db_connect", wraps=original) as opened:
                queries._list_databases(str(active.parent), "Current")
            self.assertNotIn(snapshot, [call.args[0].resolve() for call in opened.call_args_list])

    def test_isolation_overrides_and_restores_discovery_roots_and_rejects_external_status(self):
        before = [module.PROJECT_ROOT for module in (queries, discovery, handler)]
        home = Path.home()
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            root = Path(directory).resolve()
            self.assertEqual([module.PROJECT_ROOT for module in (queries, discovery, handler)], [root] * 3)
            self.assertEqual(Path.home(), root / "isolated-home")
            with self.assertRaisesRegex(AssertionError, "outside its temporary root"):
                discovery.read_status({"data_dir": str(root.parent / "external-status")})
        self.assertEqual([module.PROJECT_ROOT for module in (queries, discovery, handler)], before)
        self.assertEqual(Path.home(), home)

    def test_walker_prunes_snapshot_subtrees_instead_of_only_filtering_results(self):
        from wechat_tracker.console import discovery_policy
        with tempfile.TemporaryDirectory() as directory, isolated_console(directory):
            root = Path(directory).resolve()
            visible = database(root / "visible", "Visible")
            snapshot = database(root / "before-change/deep/nested", "Snapshot")
            walk = discovery_policy.os.walk
            visited = []
            def record_walk(*args, **kwargs):
                for entry in walk(*args, **kwargs):
                    visited.append(Path(entry[0]))
                    yield entry
            with patch.object(discovery_policy.os, "walk", side_effect=record_walk):
                found = list(discovery_policy.database_candidates(root))
            self.assertEqual(found, [visible])
            self.assertFalse(any(snapshot.parent == path or root / "before-change" == path for path in visited))
