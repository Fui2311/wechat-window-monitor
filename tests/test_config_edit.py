"""Regression coverage for group changes preserving unrelated TOML data."""

from pathlib import Path
import tempfile
import tomllib
import unittest

from wechat_tracker.config import update_toml_root_keys


class GroupConfigEditTests(unittest.TestCase):
    def test_crlf_inline_comment_is_not_duplicated_on_save(self):
        original = 'group = "before" # keep comment\r\ndata_dir = "state"\r\n[capture]\r\nsource = "jsonl"\r\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_bytes(original.encode("utf-8"))
            update_toml_root_keys(path, {"group": "after"})
            result = path.read_bytes()
            self.assertNotIn(b"\r\r\n", result)
            self.assertIn(b"# keep comment\r\n", result)
            self.assertEqual(tomllib.loads(result.decode())["group"], "after")

    def test_commented_table_header_does_not_turn_nested_group_into_root_key(self):
        original = ('group = "before" # exact window title\n'
                    'data_dir = "state"\n'
                    '[capture] # native helper settings\n'
                    'group = "nested value must stay"\n'
                    'source = "macos_ax"\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(original)
            update_toml_root_keys(path, {"group": "after", "data_dir": "new-state"})
            result = path.read_text()
            parsed = tomllib.loads(result)
            self.assertEqual(parsed["group"], "after")
            self.assertEqual(parsed["capture"], tomllib.loads(original)["capture"])
            self.assertIn("# exact window title", result)
            self.assertIn("[capture] # native helper settings", result)

    def test_quoted_root_keys_and_multiline_text_preserve_other_settings(self):
        original = ('"group" = "before"\n'
                    'data_dir = "state"\n'
                    'description = """Instructions:\n'
                    'group = "example text"\n'
                    '[capture]\n'
                    '"""\n'
                    '[service]\n'
                    'include_existing = true\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(original)
            update_toml_root_keys(path, {"group": "after", "data_dir": "new-state"})
            parsed = tomllib.loads(path.read_text())
            expected = tomllib.loads(original)
            expected.update(group="after", data_dir="new-state")
            self.assertEqual(parsed, expected)


if __name__ == "__main__":
    unittest.main()
