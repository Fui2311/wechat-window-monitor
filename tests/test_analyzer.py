from datetime import datetime, timedelta
import unittest

from wechat_tracker.analyzer import MAX_FORWARD_DEPTH, analyze_message


SAMPLE = """🎉讲座主题：《科研创新是如何炼成的》
主讲嘉宾：葛仕明、丁圣勇
🕰讲座时间：2026年10月17日(周六) 14:30-16:30
🏠讲座地点：计算机学院东阶梯
💡温馨提示：
1、报名成功的同学不可无故缺席，否则将会纳入黑名单。
2、本次活动需要提前15分钟到达，不可代签、签退，中途离场时间不可超过20分钟。
3、❗本次活动可盖2个计算机学院院章"""

# Transcribed from the genuinely expanded WeChat forwarding window on
# 2026-10-15; this fixture is local test data, not a synthetic announcement.
REAL_FORWARD_BODY = """🔔【关于第十九届“勷勤论坛”教育科学学院分论坛“格物致新，问道前沿”学术科研板块之理论框架建构与绘图讲座报名成功的通知】
✨讲座主题：“格物致新，问道前沿”学术科研板块之理论框架建构与绘图
👤主讲嘉宾：余晖
🕒讲座时间：2026年10月16日（周五）19：00‑21：00
📍讲座地点：石牌校园教育科学学院125会议厅
💖💖💖温馨提示：
1⃣盖章说明：本活动可盖教育科学学院院级章【现场盖章】
2⃣请报名成功的同学提前20分钟到达现场进行签到，讲座开始后将截止签到；
3⃣本次活动请需要盖学术章的同学携带好学术卡，需完整填写个人信息并贴好照片，并提前在学术卡上写好活动名称、日期、主讲人等全部信息，否则不予盖章；
4⃣讲座结束后到签到处领取学术卡。
‼未携带学术卡的同学须完成签到、登记并留意后续通知。讲座开始24小时内不能取消报名，爽约3次将计入黑名单！
教育科学学院研究生会
2026年10月15日"""


def message(text=SAMPLE, **kwargs):
    result = {"id": "m1", "group": "2026级计算机研究生通知群", "sender": "通知员", "sent_at": "2026-10-15T12:55:00+08:00", "kind": "text", "text": text}
    result.update(kwargs)
    return result


class AnalyzerTests(unittest.TestCase):
    def assert_notice(self, item):
        self.assertEqual(item["category"], "lecture_notice")
        self.assertEqual(item["status"], "noticed")
        self.assertFalse(item["needs_review"])
        self.assertEqual(item["review_reasons"], [])
        self.assertNotIn("confidence", item)

    def test_complete_message_keeps_optional_activity_time(self):
        item, = analyze_message(message())
        self.assert_notice(item)
        self.assertEqual(item["title"], "科研创新是如何炼成的")
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertEqual(item["end_at"], "2026-10-17T16:30:00+08:00")
        self.assertEqual(item["source_text"], SAMPLE)

    def test_brief_questions_and_incomplete_messages_also_notify(self):
        for text in ("今天去听讲座吗？", "讲座报名开始了", "学术报告名单出来了", "学术沙龙明天举办", "讲座时间待定"):
            with self.subTest(text=text):
                item, = analyze_message(message(text))
                self.assert_notice(item)
                self.assertEqual(item["source_text"], text)

    def test_announcement_heading_wins_over_time_field_without_explicit_topic(self):
        text = ("【关于某学院分论坛之“学以致用”行业前沿之从研究视角谈协同创新报名成功通知】\n"
                "主讲嘉宾：某教授\n讲座时间：2026年10月12日10:00\n讲座地点：教学楼225室")
        item, = analyze_message(message(text))
        self.assertEqual(item["title"], "从研究视角谈协同创新")
        self.assertEqual(item["summary"], item["title"])
        self.assertEqual(item["source_text"], text)
        self.assert_notice(item)

    def test_speaker_biography_is_not_a_talk_abstract(self):
        text = SAMPLE + "\n主讲人简介：某教授，博士生导师，发表论文200篇。\n曾主持多项课题。"
        item, = analyze_message(message(text))
        self.assertEqual(item["summary"], "科研创新是如何炼成的")
        self.assertNotIn("summary", item["evidence"])

    def test_flattened_activity_labels_do_not_leak_into_the_title(self):
        text = ("【活动报名】某论坛🌟活动主题：多体计算🎓活动嘉宾：某教授"
                "👥活动对象：全体研究生⏰活动时间：2026年10月16日10:30"
                "🏠活动地点：理科楼302室🖥讲座形式：线下讲座")
        item, = analyze_message(message(text))
        self.assertEqual(item["title"], "多体计算")
        self.assertEqual(item["summary"], "多体计算")
        self.assertEqual(item["start_at"], "2026-10-16T10:30:00+08:00")

    def test_real_content_excerpt_is_short_and_stops_before_speaker_biography(self):
        text = SAMPLE + "\n内容简介：本次讲座介绍如何发现研究问题。并分享研究设计的方法。\n主讲人简介：某教授，博士生导师。"
        item, = analyze_message(message(text))
        self.assertIn("如何发现研究问题", item["summary"])
        self.assertNotIn("博士生导师", item["summary"])
        self.assertLessEqual(len(item["evidence"]["summary"]), 161)

    def test_unrelated_messages_do_not_notify(self):
        for text in ("收到，谢谢", "今晚吃什么", "活动取消", "", "群聊的聊天记录",
                     "周末生活分享会报名", "游戏论坛新增了一篇攻略"):
            self.assertEqual(analyze_message(message(text)), [])
        self.assertEqual(analyze_message(None), [])

    def test_academic_sharing_clues_notify_without_the_word_lecture(self):
        for text in ("研途分享会报名通知", "科研经验分享会报名开始了", "学术分享交流会通知"):
            with self.subTest(text=text):
                item, = analyze_message(message(text))
                self.assert_notice(item)
                self.assertIsNone(item["start_at"])

    def test_unexpanded_school_forum_preview_still_creates_an_honest_notice(self):
        text = ("聊天记录群聊的聊天记录: 🔔【关于石牌校园第十九届“勷勤论坛”"
                "“青年兀对论”研途分享会板块之“研途叙语：求索科研前路，笃行青年创新”研途分享会报名通知】")
        item, = analyze_message(message(text, kind="forward", sent_at=None))
        self.assert_notice(item)
        self.assertIn("研途叙语", item["source_text"])
        self.assertIn("没有可读取的子消息正文", item["evidence"]["content_note"])
        self.assertIsNone(item["start_at"])
        self.assertIsNone(item["signup_start_at"])

    def test_cancellation_reschedule_and_signup_rules_are_only_message_text(self):
        for text in ("《科研方法》讲座取消", "《科研方法》讲座改期，日期另行通知", "本讲座不会取消", "讲座报名后无法取消预约", "本次讲座按抽签决定名单"):
            with self.subTest(text=text):
                item, = analyze_message(message(text))
                self.assert_notice(item)
                self.assertIn(text, item["source_text"])
                self.assertNotIn("status", item["evidence"])

    def test_real_personal_withdrawal_clause_never_cancels_lecture(self):
        policy = "如不能参加该活动，请记得在距离讲座活动开始48小时前联系所在学院学术部取消本次活动，请备注姓名，学院、学号。讲座开始前24小时内不可取消预约。"
        item, = analyze_message(message(SAMPLE + "\n" + policy))
        self.assert_notice(item)
        self.assertIn(policy, item["source_text"])
        self.assertTrue(item["schedule_ready"])

    def test_signup_window_takes_first_endpoint(self):
        item, = analyze_message(message(SAMPLE + "\n报名时间：2026年10月16日09:00-18:00"))
        self.assertEqual(item["signup_start_at"], "2026-10-16T09:00:00+08:00")
        self.assertEqual(item["signup_start_text"], "2026年10月16日09:00")
        self.assert_notice(item)

    def test_real_month_day_signup_window_remains_readable_without_sending_time(self):
        item, = analyze_message(message(SAMPLE + "\n📝报名时间：10月17日（周六）15:00‑18:00", sent_at=None))
        self.assertEqual(item["signup_start_text"], "10月17日（周六）15:00")
        self.assertIsNone(item["signup_start_at"])
        self.assert_notice(item)

    def test_signup_start_labels_and_prose(self):
        for line in ("报名开始时间：2026年10月16日09:00", "预约开放时间：2026年10月16日09:00", "2026年10月16日09:00开放报名"):
            with self.subTest(line=line):
                item, = analyze_message(message(SAMPLE + "\n" + line))
                self.assertEqual(item["signup_start_at"], "2026-10-16T09:00:00+08:00")

    def test_deadlines_and_checkin_times_are_never_signup_start(self):
        for line in ("报名截止：2026年10月16日18:00", "报名时间：截止到10月18日12:00", "签到开始时间：10月17日14:00", "讲座开始前24小时内不可取消预约"):
            with self.subTest(line=line):
                item, = analyze_message(message(SAMPLE + "\n" + line))
                self.assertIsNone(item["signup_start_at"])
                self.assertEqual(item["signup_start_text"], "")
                self.assert_notice(item)

    def test_partial_signup_time_is_copied_without_blocking(self):
        item, = analyze_message(message("讲座报名时间：明天14:30", sent_at=None))
        self.assertIsNone(item["signup_start_at"])
        self.assertIn("明天14:30", item["signup_start_text"])
        self.assert_notice(item)

    def test_labelled_signup_link_wins_over_other_urls(self):
        item, = analyze_message(message(SAMPLE + "\n讲座详情：https://example.com/talk\n报名链接：https://example.com/signup?a=1&b=2。"))
        self.assertEqual(item["signup_url"], "https://example.com/signup?a=1&b=2")

    def test_multiline_and_unlabelled_signup_links(self):
        for line in ("报名链接：\nhttps://example.com/signup", "请报名 https://example.com/signup。"):
            with self.subTest(line=line):
                item, = analyze_message(message(SAMPLE + "\n" + line))
                self.assertEqual(item["signup_url"], "https://example.com/signup")

    def test_no_arbitrary_or_executable_link_is_invented(self):
        for line in ("报名链接：javascript:alert(1)", "参考：https://example.com/a 和 https://example.com/b", "https://example.com/talk"):
            with self.subTest(line=line):
                item, = analyze_message(message("讲座通知\n" + line))
                self.assertIsNone(item["signup_url"])

    def test_flat_accessibility_fields_keep_signup_separate(self):
        item, = analyze_message(message("讲座主题：《测试》 讲座时间：2026年10月17日14:30 报名时间：2026年10月16日09:00-18:00 报名链接：https://example.com/signup"))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertEqual(item["signup_start_at"], "2026-10-16T09:00:00+08:00")
        self.assertEqual(item["signup_url"], "https://example.com/signup")

    def test_unresolved_activity_times_do_not_block_notice_or_invent_dates(self):
        for body in (SAMPLE.replace("2026年", ""), "讲座时间：明天14:30", "讲座时间：2026年2月30日25:30", SAMPLE + "\n讲座时间：2026年10月18日18:30"):
            with self.subTest(body=body):
                item, = analyze_message(message(body, sent_at=None))
                self.assert_notice(item)
                self.assertIsNone(item["start_at"])
                self.assertFalse(item["schedule_ready"])
                self.assertTrue(item["activity_time_text"])

    def test_full_date_needs_no_message_timestamp(self):
        item, = analyze_message(message(sent_at=None))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assert_notice(item)

    def test_relative_date_uses_only_original_message_time(self):
        item, = analyze_message(message("讲座时间：明天14:30", sent_at="2024-12-31T16:30:00Z"))
        self.assertEqual(item["start_at"], "2025-01-02T14:30:00+08:00")

    def test_fullwidth_times_and_afternoon_shorthand(self):
        for value in ("14：30～16：30", "下午2:30-4:30"):
            item, = analyze_message(message(SAMPLE.replace("14:30-16:30", value)))
            self.assertEqual(item["end_at"], "2026-10-17T16:30:00+08:00")

    def test_bad_weekday_or_cross_midnight_does_not_change_notice_status(self):
        for text in (SAMPLE.replace("周六", "周日"), SAMPLE.replace("14:30-16:30", "23:30-00:30")):
            item, = analyze_message(message(text))
            self.assert_notice(item)
            self.assertFalse(item["schedule_ready"])

    def test_attachment_name_is_a_sufficient_lecture_clue(self):
        item, = analyze_message(message("", kind="file", attachments=[{"kind": "file", "name": "讲座报名名单.pdf"}]))
        self.assert_notice(item)
        self.assertIn("讲座报名名单.pdf", item["summary"])
        self.assertIsNone(item["start_at"])
        self.assertEqual(item["evidence"]["content_note"], "文件消息；仅记录文件名，未下载、未读取文件正文")

    def test_forward_summary_is_a_notice_without_claiming_to_read_body(self):
        item, = analyze_message(message("聊天记录：明天讲座报名", kind="forward"))
        self.assert_notice(item)
        self.assertIsNone(item["start_at"])
        self.assertIn("转发卡片", item["evidence"]["content_note"])
        self.assertEqual(analyze_message(message("群聊的聊天记录", kind="forward")), [])

    def test_forward_children_retain_original_metadata(self):
        item, = analyze_message(message("聊天记录", kind="forward", children=[{"text": SAMPLE, "kind": "text"}]))
        self.assert_notice(item)
        self.assertEqual(item["source_message_id"], "m1/child-1")
        self.assertEqual(item["source_group"], message()["group"])
        self.assertIsNone(item["source_sender"])
        self.assertIsNone(item["source_sent_at"])

    def test_forward_does_not_borrow_parent_time(self):
        item, = analyze_message(message("聊天记录", kind="forward", children=[{"text": "明天14:30学术报告", "kind": "text"}]))
        self.assertIsNone(item["start_at"])
        self.assert_notice(item)

    def test_real_forward_still_notifies_in_flat_or_multiline_form(self):
        for text in (REAL_FORWARD_BODY, " ".join(REAL_FORWARD_BODY.splitlines())):
            item, = analyze_message(message("群聊的聊天记录", kind="forward", children=[{"text": text}]))
            self.assert_notice(item)
            self.assertEqual(item["start_at"], "2026-10-16T19:00:00+08:00")
            self.assertEqual(item["end_at"], "2026-10-16T21:00:00+08:00")

    def test_cycles_and_deep_forward_trees_terminate(self):
        node = message("讲座转发", kind="forward")
        node["children"] = [node]
        item, = analyze_message(node)
        self.assert_notice(item)
        self.assertIn("循环", item["evidence"]["content_note"])
        for _ in range(MAX_FORWARD_DEPTH + 2):
            node = {"kind": "forward", "text": "讲座转发", "children": [node]}
        items = analyze_message(node)
        self.assertEqual(len(items), 1)
        self.assert_notice(items[0])

    def test_contract_has_signup_fields_and_no_confidence_or_deadline(self):
        item, = analyze_message(message())
        for field in ("signup_start_at", "signup_start_text", "signup_url", "source_text"):
            self.assertIn(field, item)
        for field in ("confidence", "deadline_at", "deadline_text"):
            self.assertNotIn(field, item)


if __name__ == "__main__":
    unittest.main()
