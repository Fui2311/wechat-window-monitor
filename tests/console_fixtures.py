"""Confine automatic console discovery to each test's temporary tree."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import json
from unittest.mock import patch


@contextmanager
def isolated_console(root):
    root = Path(root).resolve()
    def stored_status(config):
        directory = Path(config["data_dir"]).resolve()
        if not directory.is_relative_to(root):
            raise AssertionError("Console fixture attempted status outside its temporary root")
        try:
            return json.loads((directory / "service.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"running": False, "status": "stopped"}

    with ExitStack() as scope:
        for module in ("queries", "discovery", "handler"):
            scope.enter_context(patch(f"wechat_tracker.console.{module}.PROJECT_ROOT", root))
        # These modules import the same Path class; this patch is test-process local.
        scope.enter_context(patch.object(Path, "home", return_value=root / "isolated-home"))
        scope.enter_context(patch("wechat_tracker.console.discovery.read_status", side_effect=stored_status))
        yield
