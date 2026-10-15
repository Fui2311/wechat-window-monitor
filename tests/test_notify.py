import os
import smtplib
import ssl
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from wechat_tracker.notify import (
    EmailConfigurationError,
    EmailNotificationError,
    format_notification,
    send_email,
)


class SendEmailTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_security": "ssl",
            "smtp_user_env": "TRACKER_SMTP_USER",
            "smtp_password_env": "TRACKER_SMTP_PASSWORD",
            "from_env": "TRACKER_EMAIL_FROM",
            "to_env": "TRACKER_EMAIL_TO",
        }
        self.environment = {
            "TRACKER_SMTP_USER": "sender@example.com",
            "TRACKER_SMTP_PASSWORD": "test-secret-do-not-log",
            "TRACKER_EMAIL_FROM": "sender@example.com",
            "TRACKER_EMAIL_TO": "one@example.com, two@example.com",
        }
        self.env_patch = patch.dict(os.environ, self.environment, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    @patch("wechat_tracker.notify.smtplib.SMTP")
    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_ssl_sends_utf8_and_all_recipients(self, smtp_ssl, smtp_plain):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        send_email("讲座提醒：科研创新", "地点：计算机学院东阶梯\n提前 15 分钟到达。", self.config)
        args, kwargs = smtp_ssl.call_args
        self.assertEqual(args, ("smtp.example.com", 465))
        self.assertEqual(kwargs["timeout"], 15)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)
        smtp_plain.assert_not_called()
        smtp.starttls.assert_not_called()
        smtp.login.assert_called_once_with("sender@example.com", "test-secret-do-not-log")
        message = smtp.send_message.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(str(parsed["Subject"]), "讲座提醒：科研创新")
        self.assertIn("计算机学院东阶梯", parsed.get_content())
        self.assertEqual(parsed.get_content_charset(), "utf-8")
        self.assertFalse(parsed.is_multipart())
        self.assertEqual(smtp.send_message.call_args.kwargs["to_addrs"], ["one@example.com", "two@example.com"])

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    @patch("wechat_tracker.notify.smtplib.SMTP")
    def test_starttls_precedes_login_and_uses_timeout(self, smtp_plain, smtp_ssl):
        self.config.update(smtp_security="starttls", smtp_port=587, smtp_timeout=8)
        smtp = smtp_plain.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        send_email("测试", "正文", self.config)
        smtp_plain.assert_called_once_with("smtp.example.com", 587, timeout=8)
        self.assertEqual([entry[0] for entry in smtp.method_calls], ["ehlo", "starttls", "ehlo", "login", "send_message"])
        tls_context = smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(tls_context.check_hostname)
        self.assertEqual(tls_context.verify_mode, ssl.CERT_REQUIRED)
        smtp_ssl.assert_not_called()

    @patch("wechat_tracker.notify.smtplib.SMTP")
    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_bad_configuration_never_connects(self, smtp_ssl, smtp_plain):
        variants = [
            {**self.config, "password": "accidental-secret"},
            {key: value for key, value in self.config.items() if key != "smtp_host"},
            {**self.config, "smtp_security": "none"},
            {**self.config, "smtp_security": []},
            {**self.config, "smtp_port": True},
            {**self.config, "smtp_timeout": float("nan")},
            {**self.config, "smtp_password_env": "invalid name"},
        ]
        for config in variants:
            with self.subTest(config=config):
                with self.assertRaises(EmailConfigurationError):
                    send_email("测试", "正文", config)
        del os.environ["TRACKER_SMTP_PASSWORD"]
        with self.assertRaises(EmailConfigurationError):
            send_email("测试", "正文", self.config)
        smtp_ssl.assert_not_called()
        smtp_plain.assert_not_called()

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_invalid_recipient_or_header_never_connects(self, smtp_ssl):
        for recipient in ("bad-address", "a@@example.com", "a@example..com", "a@example.com,", "a@example.com\nBcc: victim@example.com", ""):
            with self.subTest(recipient=recipient):
                os.environ["TRACKER_EMAIL_TO"] = recipient
                with self.assertRaises(EmailConfigurationError):
                    send_email("测试", "正文", self.config)
        os.environ["TRACKER_EMAIL_TO"] = "one@example.com"
        with self.assertRaises(EmailConfigurationError):
            send_email("测试\r\nBcc: victim@example.com", "正文", self.config)
        smtp_ssl.assert_not_called()

    @patch("wechat_tracker.notify.smtplib.SMTP")
    def test_tls_failure_never_logs_in_or_downgrades(self, smtp_plain):
        self.config.update(smtp_security="starttls", smtp_port=587)
        smtp = smtp_plain.return_value.__enter__.return_value
        smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("STARTTLS unavailable")
        with self.assertRaises(EmailNotificationError):
            send_email("测试", "正文", self.config)
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()
        self.assertEqual(smtp_plain.call_count, 1)

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_provider_exception_does_not_expose_password(self, smtp_ssl):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"test-secret-do-not-log")
        with self.assertRaises(EmailNotificationError) as raised:
            send_email("测试", "正文", self.config)
        self.assertNotIn("test-secret", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        smtp.send_message.assert_not_called()

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_partial_recipient_refusal_is_failure(self, smtp_ssl):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.send_message.return_value = {"two@example.com": (550, b"no")}
        with self.assertRaises(EmailNotificationError):
            send_email("测试", "正文", self.config)


class FormatNotificationTests(unittest.TestCase):
    def test_event_contains_arrival_requirements_and_source(self):
        subject, body = format_notification(
            {
                "title": "《科研创新是如何炼成的》",
                "start_at": "2026-10-17T14:30:00+08:00",
                "end_at": "2026-10-17T16:30:00+08:00",
                "arrival_at": "2026-10-17T14:15:00+08:00",
                "location": "计算机学院东阶梯",
                "speakers": ["葛仕明", "丁圣勇"],
                "requirements": ["报名成功的同学不可无故缺席", "不可代签到、签退"],
                "source_group": "2026级计算机研究生通知群",
                "source_sender": "26-计科-黄苏婷",
                "source_text": "讲座时间：2026年10月17日 14:30—16:30",
                "version": 2,
                "status": "active",
            },
            "reminder",
        )
        self.assertIn("到时提醒", subject)
        self.assertIn("建议到达：2026-10-17 14:15（北京时间）", body)
        self.assertIn("不可代签到", body)
        self.assertIn("来源群：2026级计算机研究生通知群", body)
        self.assertIn("无法确认你是否报名成功或在名单内", body)
        self.assertNotIn("你已报名成功", body)

    def test_missing_fields_and_review_reason_remain_explicit(self):
        subject, body = format_notification({"needs_review": True, "review_reasons": ["年份不明确"]}, "review")
        self.assertIn("未识别标题", subject)
        self.assertIn("年份不明确", body)
        self.assertIn("未识别活动开始时间", body)
        self.assertIn("来源原文未提供", body)

    def test_all_reason_types_and_cancellation(self):
        for reason in ("new", "update", "cancelled", "reminder", "review"):
            subject, body = format_notification({}, reason)
            self.assertTrue(subject)
            self.assertTrue(body)
            if reason == "cancelled":
                self.assertIn("已取消", body)
        with self.assertRaises(EmailConfigurationError):
            format_notification({}, "surprise")

    def test_subject_is_one_line_and_evidence_is_bounded(self):
        subject, body = format_notification({"title": "讲座\n变更", "source_text": "长" * 1500}, "update")
        self.assertNotIn("\n", subject)
        self.assertIn("截取摘要", body)
        self.assertLess(body.count("长"), 1100)


if __name__ == "__main__":
    unittest.main()
