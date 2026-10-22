from datetime import datetime, timedelta, timezone
import unittest
from wechat_tracker.web import _delivery_info


class DeliveryStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,10,19,0,0,tzinfo=timezone.utc)
        self.ready = dict(running=True, send_enabled=True, send_ready=True, email_state='ready')
        self.job = dict(status='pending', attempts=0, last_error=None, due_at=self.now.isoformat())

    def test_missing_credentials_is_blocked_without_failed_attempt(self):
        info = _delivery_info(self.job, dict(self.ready,send_ready=False,email_state='not_configured'),self.now)
        self.assertEqual(info['state'],'blocked_credentials')
        self.assertEqual(info['attempts'],0)
        self.assertIn('尚未尝试',info['message'])

    def test_keychain_error_is_explained(self):
        info = _delivery_info(self.job,dict(self.ready,send_ready=False,email_state='credential_access_failed'),self.now)
        self.assertIn('Keychain',info['message'])

    def test_retry_has_time_and_error(self):
        due=(self.now+timedelta(minutes=1)).isoformat()
        info=_delivery_info(dict(self.job,attempts=1,last_error='EmailNotificationError',due_at=due),self.ready,self.now)
        self.assertEqual(info['state'],'retry_wait')
        self.assertEqual(info['retry_at'],due)
        self.assertEqual(info['error'],'EmailNotificationError')

    def test_queue_and_delayed_queue(self):
        self.assertEqual(_delivery_info(self.job,self.ready,self.now)['state'],'queued')
        self.assertEqual(_delivery_info(self.job,self.ready,self.now+timedelta(seconds=31))['state'],'queued_late')

    def test_sent_is_success_even_after_service_stops(self):
        info=_delivery_info(dict(self.job,status='sent'),{},self.now)
        self.assertEqual(info['state'],'sent')
        self.assertIn('SMTP',info['label'])

    def test_stopped_preview_sending_and_terminal_failure(self):
        self.assertEqual(_delivery_info(self.job,{},self.now)['state'],'blocked_stopped')
        self.assertEqual(_delivery_info(self.job,dict(self.ready,send_enabled=False),self.now)['state'],'preview_only')
        self.assertEqual(_delivery_info(dict(self.job,status='sending'),self.ready,self.now)['state'],'sending')
        self.assertEqual(_delivery_info(dict(self.job,status='dead',attempts=5),self.ready,self.now)['state'],'failed')
