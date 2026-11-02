import unittest

from wechat_monitor.mail_format import display_time, format_match_mail


class MailFormatTests(unittest.TestCase):
    def test_source_and_capture_time_remain_distinct(self):
        self.assertEqual(display_time("2026-11-02T02:26:23Z"), "2026-11-02 10:26:23")
        _, plain, html = format_match_mail(
            {"name": "邮件测试"},
            {"text": "通知", "observed_at": "2026-11-02T02:26:23Z"},
            {"reason": "命中"},
            record_id=1,
        )
        self.assertIn("消息发送时间：未读取", plain)
        self.assertIn("采集时间：2026-11-02 10:26:23", plain)
        self.assertIn("发送时间未从微信读取", html)
        self.assertIn("待核对", display_time("2026-11-02T10:26:23"))

    def test_untrusted_content_is_escaped_and_source_time_preserved(self):
        _, plain, html = format_match_mail(
            {"name": "群<script>"},
            {
                "text": "<img src=x>补盖章",
                "sender": "<b>成员</b>",
                "sent_at": "2026-11-02T10:00:00+08:00",
                "children": [{"text": "子消息", "sent_at": "2026-11-01T12:00:00Z"}],
            },
            {"reason": "<script>不执行</script>", "evidence": "补盖章"},
        )
        self.assertIn("消息发送时间：2026-11-02 10:00:00", plain)
        self.assertIn("转发内消息发送时间：2026-11-01 20:00:00", plain)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x>", html)
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertIn("<mark ", html)
        self.assertIn("消息原文", html)
