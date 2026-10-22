import unittest

from wechat_tracker.notify import format_notification, format_notification_html, notification_format


class NoticeMailFormatTests(unittest.TestCase):
    def notice(self, **updates):
        event = dict(category='lecture_notice', processing_method='llm', title='人工智能研究交流',
                     summary='介绍研究方法。', source_text='【学术报告】原始通知\n报名链接：https://example.com/signup?a=1&b=2',
                     start_at='2026-10-20T14:00:00+08:00', end_at='2026-10-20T16:00:00+08:00',
                     signup_start_at='2026-10-19T09:00:00+08:00', signup_url='https://example.com/signup?a=1&b=2')
        event.update(updates)
        return event

    def test_llm_text_and_html_contain_extraction_then_source(self):
        event = self.notice()
        subject, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertEqual(subject, '[讲座·LLM] 人工智能研究交流')
        for label in ('讲座主题', '内容摘要', '讲座活动时间', '报名开始时间', '报名链接'):
            self.assertIn(label, body)
            self.assertIn(label, html)
        self.assertIn('2026-10-19 09:00', body)
        self.assertIn('2026-10-20 14:00', body)
        self.assertIn('2026-10-20 16:00', body)
        self.assertTrue(body.endswith(event['source_text']))
        self.assertLess(html.index('id="llm-extraction"'), html.index('id="source-text"'))
        self.assertIn('href="https://example.com/signup?a=1&amp;b=2"', html)
        self.assertEqual(notification_format(event), 'llm_summary')

    def test_rules_keep_exact_source_even_with_extracted_fields(self):
        event = self.notice(processing_method='rules')
        subject, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertEqual(body, event['source_text'])
        self.assertEqual(subject, '[讲座] 【学术报告】原始通知')
        self.assertNotIn('id="llm-extraction"', html)
        self.assertNotIn('介绍研究方法', html)

    def test_missing_times_do_not_block_llm_notice(self):
        event = self.notice(start_at=None, end_at=None, signup_start_at=None, signup_url=None, summary='')
        subject, body = format_notification(event, 'new')
        self.assertIn('[讲座·LLM]', subject)
        for label in ('内容摘要', '讲座活动时间', '报名开始时间', '报名链接'):
            self.assertIn(label+'：未注明', body)
        self.assertNotIn('报名截止时间', body)

    def test_partial_time_text_is_retained(self):
        event = self.notice(start_at=None, end_at=None, signup_start_at=None,
                            activity_time_text='明天下午', signup_start_text='今天中午开放报名')
        _, body = format_notification(event, 'new')
        self.assertIn('讲座活动时间：明天下午', body)
        self.assertIn('报名开始时间：今天中午开放报名', body)

    def test_llm_failure_uses_source_with_explicit_fallback_note(self):
        event = self.notice(processing_method='rules_fallback', processing_error='do not echo arbitrary errors')
        _, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertTrue(body.startswith(event['source_text']))
        self.assertIn('LLM 提取失败', body)
        self.assertIn('回退本地规则', html)
        self.assertNotIn('do not echo', body)
        self.assertNotIn('id="llm-extraction"', html)

    def test_file_remains_filename_only_in_either_mode(self):
        event = self.notice(source_kind='file', title='讲座名单.pdf')
        subject, body = format_notification(event, 'new')
        self.assertEqual(subject, '[讲座] 文件：讲座名单.pdf')
        self.assertIn('未读取文件正文', body)
        self.assertNotIn('介绍研究方法', body)
        self.assertEqual(notification_format(event), 'source')

    def test_untrusted_model_markup_and_link_are_not_executable(self):
        event = self.notice(title='<img src=x onerror=alert(1)>\n讲座', summary='<script>alert(1)</script>',
                            signup_url='javascript:alert(1)')
        subject, body = format_notification(event, 'new')
        html = format_notification_html(event, 'new')
        self.assertNotIn('\n', subject)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn('报名链接：未注明', body)

    def test_extracted_link_must_exist_in_source(self):
        event = self.notice(signup_url='https://wrong.example/signup')
        _, body = format_notification(event, 'new')
        self.assertIn('报名链接：未注明', body)
        self.assertNotIn('wrong.example', format_notification_html(event, 'new'))
