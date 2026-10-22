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
    format_notification_html,
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
        # Windows OpenSSL requires these to locate its system providers.
        self.environment.update({key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ})
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

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_html_email_keeps_utf8_plain_text_alternative(self, smtp_ssl):
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.send_message.return_value = {}
        send_email("讲座通知", "讲座消息：测试\n\n报名链接：https://example.com", self.config,
                   html_body="<html><body><h1>测试讲座</h1></body></html>")
        message = smtp.send_message.call_args.args[0]
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(parsed.get_content_type(), "multipart/alternative")
        self.assertEqual([p.get_content_type() for p in parsed.iter_parts()], ["text/plain", "text/html"])
        self.assertIn("讲座消息：测试\n\n报名链接", parsed.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("<h1>测试讲座</h1>", parsed.get_body(preferencelist=("html",)).get_content())

    @patch("wechat_tracker.notify.smtplib.SMTP_SSL")
    def test_invalid_html_body_never_connects(self, smtp_ssl):
        with self.assertRaises(EmailConfigurationError):
            send_email("测试", "正文", self.config, html_body={})
        smtp_ssl.assert_not_called()


class FormatNotificationTests(unittest.TestCase):
    def test_body_contains_exactly_three_fields(self):
        subject, body = format_notification({
            "title": "科研创新", "summary": "讲座《科研创新》：介绍研究问题的发现方法。",
            "start_at": "2026-10-17T14:30:00+08:00", "end_at": "2026-10-17T16:30:00+08:00",
            "deadline_at": "2026-10-16T18:00:00+08:00", "schedule_ready": True,
            "arrival_at": "2026-10-17T14:15:00+08:00", "location": "东阶梯",
            "speakers": ["某专家"], "requirements": ["签到"], "source_text": "名单原文",
        }, "reminder")
        self.assertIn("到时提醒", subject)
        self.assertEqual([line.split("：", 1)[0] for line in body.splitlines()],
                         ["讲座摘要", "报名截止时间", "讲座活动时间"])
        self.assertIn("研究问题的发现方法", body)
        self.assertIn("2026-10-16 18:00", body)
        self.assertIn("2026-10-17 14:30", body)
        for irrelevant in ("14:15", "东阶梯", "某专家", "签到", "名单", "来源群", "发送者"):
            self.assertNotIn(irrelevant, body)

    def test_missing_times_are_explicit_and_never_invented(self):
        subject, body = format_notification({}, "review")
        self.assertIn("未识别标题", subject)
        self.assertIn("报名截止时间：未注明", body)
        self.assertIn("讲座活动时间：未注明（待核实）", body)
        self.assertNotIn("00:00", body)

    def test_partial_times_keep_evidence_with_uncertainty(self):
        _, body = format_notification({"title": "测试", "deadline_text": "10月16日",
                                       "activity_time_text": "明天 / 14:30", "schedule_ready": False}, "review")
        self.assertIn("报名截止时间：10月16日（待核实）", body)
        self.assertIn("讲座活动时间：明天 / 14:30（待核实）", body)

    def test_all_reasons_and_cancelled_time_are_clear(self):
        for reason in ("new", "update", "cancelled", "reminder", "review"):
            subject, body = format_notification({"title": "测试", "start_at": "2026-10-17T14:30:00+08:00"}, reason)
            self.assertTrue(subject)
            self.assertEqual(len(body.splitlines()), 3)
            if reason == "cancelled":
                self.assertIn("【已取消】", body)
                self.assertIn("原定", body)
        with self.assertRaises(EmailConfigurationError):
            format_notification({}, "surprise")

    def test_source_and_summary_are_bounded_and_single_line(self):
        subject, body = format_notification({"title": "讲座\n变更", "summary": "摘要\n" + "长" * 1500,
                                           "source_text": "不应输出原文" * 1500}, "update")
        self.assertNotIn("\n", subject)
        self.assertEqual(len(body.splitlines()), 3)
        self.assertLess(body.count("长"), 501)
        self.assertNotIn("不应输出原文", body)

    def test_notice_body_is_exact_source_including_paragraphs_emoji_and_spaces(self):
        raw = ("📣【讲座报名通知】\n\n主题：原文标题\n  主讲人：某教授\n"
               "讲座时间：10月22日 15:00-17:30\n温馨提示：请提前到场。\n")
        event = dict(category="lecture_notice", title="错误拆分标题", source_text=raw,
                     summary="错误摘要", signup_start_text="错误时间", signup_url="https://wrong.example",
                     start_at="bad-date", schedule_ready=True)
        with patch('wechat_tracker.presentation.notifications._display_datetime', side_effect=AssertionError('must not parse fields')):
            subject, body = format_notification(event, 'new')
            html = format_notification_html(event, 'new')
        self.assertEqual(subject, '[讲座] 📣【讲座报名通知】')
        self.assertEqual(body, raw)
        self.assertIn('white-space:pre-wrap', html)
        for value in ('错误拆分标题', '错误摘要', '错误时间', 'wrong.example', '报名开始时间'):
            self.assertNotIn(value, body)
            self.assertNotIn(value, html)

    def test_missing_source_is_disclosed_without_inventing_original_from_summary(self):
        event = dict(category='lecture_notice', title='已有标题', summary='自动生成的摘要',
                     deadline_at='2026-10-17T18:00:00+08:00')
        subject, body = format_notification(event, 'new')
        self.assertEqual(subject, '[讲座] 已有标题')
        self.assertEqual(body, '原文未保存。')
        self.assertNotIn('自动生成的摘要', format_notification_html(event, 'new'))

    def test_unread_forward_keeps_its_actual_preview_and_missing_body_notice(self):
        note = '转发卡片正文未读取，需展开核对 <原文>'
        raw = '聊天记录：研途分享会讲座通知'
        event = dict(category='lecture_notice', source_text=raw, evidence=dict(content_note=note))
        _, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertEqual(body, raw + '\n\n提示：' + note)
        self.assertIn('&lt;原文&gt;', html)
        self.assertNotIn('<原文>', html)
        for generated in ('未注明', '报名开始时间', '讲座活动时间', '未从已读内容中取得'):
            self.assertNotIn(generated, body)

    def test_subject_uses_first_source_line_and_only_subject_is_bounded(self):
        raw = '【讲座通知】' + '长' * 200 + '\n\n' + '完整正文' * 500 + '\n最后一句也要保留'
        subject, body = format_notification(dict(category='lecture_notice', title='错误标题', source_text=raw), 'new')
        self.assertTrue(subject.startswith('[讲座] 【讲座通知】'))
        self.assertNotIn('\n', subject)
        self.assertLessEqual(len(subject), 85)
        self.assertEqual(body, raw)
        self.assertTrue(body.endswith('最后一句也要保留'))

    def test_raw_time_and_requirements_are_not_rewritten_or_removed(self):
        raw = '测试讲座\n讲座时间：10月19日15:00-16:00\n报名截止：明天\n必须签到、携带学术卡。'
        event = dict(category='lecture_notice', source_text=raw, start_at='2026-10-19T15:00:00+08:00',
                     end_at='2026-10-19T16:00:00+08:00', schedule_ready=True)
        _, body = format_notification(event, 'new')
        self.assertEqual(body, raw)
        self.assertNotIn('北京时间', body)

    def test_html_escapes_source_and_never_executes_source_markup(self):
        raw = '<script>讲座</script>\n<img src=x onerror="alert(1)">\n地址 javascript:alert(1)'
        event = dict(category='lecture_notice', source_text=raw)
        _, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertEqual(body, raw)
        self.assertIn('&lt;script&gt;讲座&lt;/script&gt;', html)
        self.assertIn('&lt;img', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('href="javascript:', html)

    def test_html_preserves_literal_signup_url_in_place(self):
        raw = '讲座报名\n报名链接：https://example.com/signup?a=1&b=2#form\n请自行查看确认名单。'
        event = dict(category='lecture_notice', source_text=raw, signup_url='https://wrong.example')
        _, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertEqual(body, raw)
        self.assertIn('href="https://example.com/signup?a=1&amp;b=2#form"', html)
        self.assertIn('请自行查看确认名单。', html)
        self.assertNotIn('wrong.example', html)
        self.assertNotIn('打开报名链接', html)
        self.assertNotIn('@import', html)

    def test_html_source_text_roundtrips_without_losing_spaces_or_linebreaks(self):
        from html.parser import HTMLParser
        class SourceReader(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.inside = False
                self.parts = []
            def handle_starttag(self, tag, attrs):
                if tag == 'div' and dict(attrs).get('id') == 'source-text':
                    self.inside = True
            def handle_endtag(self, tag):
                if tag == 'div':
                    self.inside = False
            def handle_data(self, data):
                if self.inside:
                    self.parts.append(data)
        raw = '  📣讲座 <原文>\n\n甲 & 乙\t保留空格\nhttps://example.com/?a=1&b=2\n'
        reader = SourceReader()
        reader.feed(format_notification_html(dict(category='lecture_notice', source_text=raw), 'new'))
        self.assertEqual(''.join(reader.parts), raw)


if __name__ == "__main__":
    unittest.main()
