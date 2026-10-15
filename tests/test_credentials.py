import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_tracker.credentials import apply_credentials, save_credentials, _dpapi

FIELDS = dict(smtp_user_env="TEST_WECHAT_USER", smtp_password_env="TEST_WECHAT_PASSWORD",
              from_env="TEST_WECHAT_FROM", to_env="TEST_WECHAT_TO")
VALUES = dict(smtp_user_env="sender@example.com", smtp_password_env="test-only-placeholder",
              from_env="sender@example.com", to_env="recipient@example.com")


class CredentialsTests(unittest.TestCase):
    def test_missing_credentials_do_not_become_configured(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self.assertFalse(apply_credentials(dict(data_dir=directory, email=FIELDS)))

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_windows_saved_file_is_encrypted_and_loads_for_same_account(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = dict(data_dir=directory, email=FIELDS)
            save_credentials(config, VALUES)
            stored = (Path(directory) / "email.credentials").read_bytes()
            self.assertNotIn(VALUES["smtp_password_env"].encode(), stored)
            self.assertNotIn(VALUES["smtp_user_env"].encode(), stored)
            self.assertTrue(apply_credentials(config))
            self.assertEqual(os.environ[FIELDS["smtp_password_env"]], VALUES["smtp_password_env"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_tampered_ciphertext_fails_without_revealing_values(self):
        with self.assertRaises(RuntimeError) as caught:
            _dpapi(b"not-a-valid-DPAPI-message", decrypt=True)
        self.assertNotIn("not-a-valid", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
