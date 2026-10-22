import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from wechat_tracker.credentials import apply_credentials, save_credentials, _dpapi, _credential_helper, authorize_credentials, credential_status

FIELDS = dict(smtp_user_env="TEST_WECHAT_USER", smtp_password_env="TEST_WECHAT_PASSWORD",
              from_env="TEST_WECHAT_FROM", to_env="TEST_WECHAT_TO")
VALUES = dict(smtp_user_env="sender@example.com", smtp_password_env="test-only-placeholder",
              from_env="sender@example.com", to_env="recipient@example.com")


class CredentialsTests(unittest.TestCase):
    def test_status_follows_source_without_loading_secrets_or_wrong_platform_marker(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            data = root/'source-data'
            data.mkdir()
            (data/'email.credentials').write_text('synthetic encrypted marker')
            source = dict(data_dir=str(data), email=FIELDS, config_path=str(root/'source.toml'), group='来源群')
            follower = dict(email_credentials_config_path='source.toml', config_path=str(root/'follower.toml'))
            with patch('wechat_tracker.config.load_config', return_value=source), \
                    patch('wechat_tracker.credentials._keychain') as keychain, \
                    patch('wechat_tracker.credentials._dpapi') as dpapi:
                with patch('wechat_tracker.credentials.sys.platform', 'win32'):
                    status = credential_status(follower)
                    self.assertTrue(status['configured'])
                    self.assertTrue(status['settings_shared'])
                    self.assertEqual(status['backend'], 'windows_dpapi')
                    self.assertEqual(status['setup_config_path'], source['config_path'])
                with patch('wechat_tracker.credentials.sys.platform', 'darwin'):
                    self.assertFalse(credential_status(follower)['configured'])
                keychain.assert_not_called()
                dpapi.assert_not_called()

    def test_missing_credentials_do_not_become_configured(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            self.assertFalse(apply_credentials(dict(data_dir=directory, email=FIELDS)))

    def test_shared_credentials_reuse_secure_source_for_target_env_names(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            main_dir=Path(directory)/'main'; main_dir.mkdir()
            (main_dir/'email.keychain').write_text('marker only')
            source=dict(group='main',data_dir=str(main_dir),email=FIELDS)
            target_fields={field:'TARGET_'+field.upper() for field in FIELDS}
            target=dict(group='test',data_dir=str(Path(directory)/'test'),email=target_fields,
                        email_credentials_config_path=str(Path(directory)/'main.toml'))
            with patch('wechat_tracker.credentials.sys.platform','darwin'), \
                 patch('wechat_tracker.config.load_config',return_value=source), \
                 patch('wechat_tracker.credentials._keychain',return_value=VALUES) as keychain:
                self.assertTrue(apply_credentials(target))
                self.assertEqual(os.environ[target_fields['smtp_password_env']],VALUES['smtp_password_env'])
                for name in list(os.environ):
                    if name.startswith('TARGET_'): del os.environ[name]
                self.assertTrue(apply_credentials(target))
                self.assertEqual(keychain.call_count,1)
            self.assertFalse((Path(target['data_dir'])/'email.credentials').exists())

    def test_marker_update_refreshes_memory_cache_without_writing_secrets(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            marker = Path(directory) / 'email.keychain'
            marker.write_text('first marker')
            config = dict(group='main', data_dir=directory, email=FIELDS)
            updated = dict(VALUES, smtp_password_env='replacement-test-placeholder')
            with patch('wechat_tracker.credentials.sys.platform', 'darwin'), \
                    patch('wechat_tracker.credentials._keychain', side_effect=[VALUES, updated]) as read:
                self.assertTrue(apply_credentials(config))
                self.assertTrue(apply_credentials(config))
                marker.write_text('updated marker version')
                self.assertTrue(apply_credentials(config))
                self.assertEqual(read.call_count, 2)
                self.assertEqual(os.environ[FIELDS['smtp_password_env']], updated['smtp_password_env'])
            self.assertNotIn(updated['smtp_password_env'], marker.read_text())

    def test_denied_or_timed_out_authorization_is_not_repeated_by_dispatch(self):
        for failure in (RuntimeError('denied'), subprocess.TimeoutExpired('helper', 120)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                (Path(directory) / 'email.keychain').write_text('marker')
                config = dict(group='main', data_dir=directory, email=FIELDS)
                with patch('wechat_tracker.credentials.sys.platform', 'darwin'), \
                        patch('wechat_tracker.credentials._keychain', side_effect=failure) as read:
                    for _ in range(3):
                        with self.assertRaises(RuntimeError):
                            apply_credentials(config)
                    read.assert_called_once()

    def test_keychain_helper_is_independent_of_capture_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / 'WeChat Lecture Tracker Capture.app/Contents/MacOS/wechat-capture'
            stable = Path(directory) / 'WeChat Lecture Tracker Credentials.app/Contents/MacOS/wechat-credentials'
            stable.parent.mkdir(parents=True)
            stable.touch()
            self.assertEqual(_credential_helper(dict(capture=dict(mac_helper=str(capture)))), stable)

    def test_explicit_authorization_invalidates_failed_read_without_loading_secrets(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            marker = Path(directory) / 'email.keychain'
            marker.write_text('marker')
            config = dict(group='main', data_dir=directory, email=FIELDS)
            with patch('wechat_tracker.credentials.sys.platform', 'darwin'), \
                    patch('wechat_tracker.credentials._credential_helper', return_value=Path('/test/wechat-credentials')), \
                    patch('wechat_tracker.credentials._keychain', side_effect=[RuntimeError('denied'), None, VALUES]) as read:
                with self.assertRaises(RuntimeError):
                    apply_credentials(config)
                self.assertEqual(authorize_credentials(config), dict(configured=True, smtp_sent=False))
                self.assertFalse(any(name in os.environ for name in FIELDS.values()))
                self.assertTrue(apply_credentials(config))
                self.assertEqual([call.args[1] for call in read.call_args_list], ['credential-get', 'credential-authorize', 'credential-get'])

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
