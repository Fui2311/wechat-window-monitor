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
    def test_screenshot_lecture(self):
        item, = analyze_message(message())
        self.assertEqual(item["title"], "科研创新是如何炼成的")
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertEqual(item["end_at"], "2026-10-17T16:30:00+08:00")
        self.assertEqual(item["arrival_at"], "2026-10-17T14:15:00+08:00")
        self.assertEqual(item["speakers"], ["葛仕明", "丁圣勇"])
        self.assertEqual(item["location"], "计算机学院东阶梯")
        self.assertFalse(item["needs_review"])
        self.assertTrue(item["schedule_ready"])
        self.assertIn("报名成功", "".join(item["requirements"]))
        self.assertEqual(item["source_message_id"], "m1")

    def test_fullwidth_time(self):
        item, = analyze_message(message(SAMPLE.replace("14:30-16:30", "14：30～16：30")))
        self.assertEqual(item["end_at"], "2026-10-17T16:30:00+08:00")

    def test_relative_date_uses_message_date(self):
        item, = analyze_message(message("讲座主题：《测试》\n讲座时间：明天14:30-16:30\n地点：A楼", sent_at="2024-12-31T16:30:00Z"))
        # Shanghai sending date is already 2025-01-01.
        self.assertEqual(item["start_at"], "2025-01-02T14:30:00+08:00")

    def test_month_day_uses_message_year(self):
        item, = analyze_message(message(SAMPLE.replace("2026年", ""), sent_at="2020-10-15T12:55:00+08:00"))
        self.assertEqual(item["start_at"], "2020-10-17T14:30:00+08:00")

    def test_no_current_year_fallback(self):
        item, = analyze_message(message(SAMPLE.replace("2026年", ""), sent_at=None))
        self.assertIsNone(item["start_at"])
        self.assertTrue(item["needs_review"])
        self.assertFalse(item["schedule_ready"])

    def test_naive_sent_at_does_not_authorize_relative_date(self):
        item, = analyze_message(message("讲座主题：《测试》\n时间：明天14:30\n地点：A", sent_at="2026-10-15T12:00:00"))
        self.assertIsNone(item["start_at"])
        self.assertTrue(item["needs_review"])

    def test_multiple_dates_are_reviewed(self):
        item, = analyze_message(message(SAMPLE + "\n讲座时间：2026年10月18日14:30"))
        self.assertIsNone(item["start_at"])
        self.assertTrue(any("多个活动日期" in text for text in item["review_reasons"]))
        self.assertFalse(item["schedule_ready"])

    def test_multiple_times_are_reviewed(self):
        item, = analyze_message(message(SAMPLE + "\n讲座时间：2026年10月17日18:00"))
        self.assertIsNone(item["start_at"])
        self.assertTrue(any("多个活动时间" in text for text in item["review_reasons"]))

    def test_two_times_without_range_marker_are_ambiguous(self):
        item, = analyze_message(message(SAMPLE.replace("14:30-16:30", "14:30 16:30")))
        self.assertIsNone(item["start_at"])
        self.assertTrue(item["needs_review"])

    def test_deadline_does_not_conflict_with_event(self):
        item, = analyze_message(message(SAMPLE + "\n报名截止：2026年10月16日18:00"))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertEqual(item["deadline_at"], "2026-10-16T18:00:00+08:00")

    def test_date_only_deadline_does_not_invent_midnight(self):
        item, = analyze_message(message(SAMPLE + "\n报名截止：10月16日"))
        self.assertIsNone(item["deadline_at"])
        self.assertTrue(item["needs_review"])

    def test_invalid_date_and_time(self):
        item, = analyze_message(message(SAMPLE.replace("10月17日", "2月30日").replace("14:30-16:30", "25:30-26:30")))
        self.assertIsNone(item["start_at"])
        self.assertTrue(item["needs_review"])

    def test_ordinary_chat_is_not_an_event(self):
        self.assertEqual(analyze_message(message("今天去听讲座吗？")), [])

    def test_missing_location_is_reviewed(self):
        item, = analyze_message(message("讲座主题：《测试》\n时间：明天14:30"))
        self.assertTrue(item["needs_review"])
        self.assertIsNone(item["location"])
        self.assertFalse(item["schedule_ready"])

    def test_forward_inherits_metadata(self):
        item, = analyze_message(message("聊天记录", kind="forward", children=[{"id": "child1", "text": SAMPLE, "kind": "text"}]))
        self.assertEqual(item["source_message_id"], "child1")
        self.assertEqual(item["source_group"], "2026级计算机研究生通知群")
        self.assertIsNone(item["source_sender"])
        self.assertIsNone(item["source_sent_at"])
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertTrue(item["schedule_ready"])

    def test_forward_relative_date_does_not_inherit_forwarding_time(self):
        item, = analyze_message(message("聊天记录", kind="forward", children=[{"text": SAMPLE.replace("2026年10月17日(周六)", "明天"), "kind": "text"}]))
        self.assertEqual(item["source_message_id"], "m1/child-1")
        self.assertIsNone(item["source_sent_at"])
        self.assertIsNone(item["start_at"])
        self.assertFalse(item["schedule_ready"])
        self.assertTrue(item["needs_review"])

    def test_forward_original_sender_and_time_are_retained(self):
        child = {"text": SAMPLE.replace("2026年10月17日(周六)", "明天"), "kind": "text", "sender": "原始通知员", "sent_at": "2026-10-16T20:00:00+08:00"}
        item, = analyze_message(message("聊天记录", kind="forward", children=[child]))
        self.assertEqual(item["source_sender"], "原始通知员")
        self.assertEqual(item["source_sent_at"], child["sent_at"])
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertTrue(item["schedule_ready"])

    def test_forward_card_is_never_a_full_announcement(self):
        item, = analyze_message(message(SAMPLE, kind="forward"))
        self.assertTrue(item["needs_review"])
        self.assertIsNone(item["start_at"])
        self.assertEqual(item["confidence"], 0.15)
        self.assertFalse(item["schedule_ready"])

    def test_forward_depth_is_bounded(self):
        node = {"text": SAMPLE, "kind": "text"}
        for _ in range(MAX_FORWARD_DEPTH + 2):
            node = {"kind": "forward", "children": [node]}
        item, = analyze_message(message("转发", kind="forward", children=[node]))
        self.assertTrue(item["needs_review"])
        self.assertIsNone(item["start_at"])

    def test_cycle_is_bounded(self):
        node = message("讲座转发", kind="forward")
        node["children"] = [node]
        item, = analyze_message(node)
        self.assertTrue(item["needs_review"])
        self.assertTrue(any("循环" in reason for reason in item["review_reasons"]))

    def test_attachment_does_not_establish_registration(self):
        item, = analyze_message(message(attachments=[{"kind": "file", "name": "《科研创新是如何炼成的》讲座报名成功名单.pdf", "text": "张三\n李四"}]))
        self.assertTrue(item["needs_review"])
        self.assertTrue(any("无法确认报名资格" in text for text in item["requirements"]))
        self.assertTrue(item["schedule_ready"])

    def test_attachment_without_body_is_review_lead(self):
        item, = analyze_message(message("", kind="file", attachments=[{"kind": "file", "name": "讲座报名成功名单.pdf"}]))
        self.assertTrue(item["needs_review"])
        self.assertIsNone(item["start_at"])
        self.assertIn("讲座报名成功名单.pdf", item["evidence"]["attachments"])
        self.assertTrue(any("无法确认报名资格" in text for text in item["requirements"]))

    def test_full_date_with_unknown_message_time_is_reviewed(self):
        item, = analyze_message(message(sent_at=None))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertTrue(item["needs_review"])
        self.assertTrue(item["schedule_ready"])
        self.assertIsNone(item["source_sent_at"])

    def test_missing_title_blocks_scheduling(self):
        item, = analyze_message(message("讲座时间：2026年10月17日14:30-16:30\n讲座地点：A楼"))
        self.assertTrue(item["needs_review"])
        self.assertFalse(item["schedule_ready"])

    def test_cancellation_keeps_title(self):
        item, = analyze_message(message("《科研创新是如何炼成的》讲座取消，请相互转告。"))
        self.assertEqual(item["status"], "cancelled")
        self.assertEqual(item["category"], "lecture_update")
        self.assertIsNone(item["start_at"])
        self.assertFalse(item["needs_review"])
        self.assertTrue(item["schedule_ready"])

    def test_registration_policy_is_not_event_cancellation(self):
        item, = analyze_message(message(SAMPLE + "\n讲座开始24小时内不能取消报名。"))
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["category"], "lecture")
        self.assertTrue(item["schedule_ready"])

    def test_explicit_no_cancellation_is_not_cancelled(self):
        item, = analyze_message(message(SAMPLE + "\n本讲座不会取消。"))
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["category"], "lecture")

    def test_labelled_event_date_excludes_signature_date(self):
        item, = analyze_message(message(SAMPLE + "\n研究生会\n2026年10月15日"))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertTrue(item["schedule_ready"])

    def assert_real_forward(self, body):
        item, = analyze_message(message("群聊的聊天记录", kind="forward", children=[{
            "text": body, "kind": "text", "sender": "转发内原始通知员", "sent_at": "2026-10-15T09:34:00+08:00",
        }]))
        self.assertEqual(item["title"], "“格物致新，问道前沿”学术科研板块之理论框架建构与绘图")
        self.assertEqual(item["speakers"], ["余晖"])
        self.assertEqual(item["start_at"], "2026-10-16T19:00:00+08:00")
        self.assertEqual(item["end_at"], "2026-10-16T21:00:00+08:00")
        self.assertEqual(item["arrival_at"], "2026-10-16T18:40:00+08:00")
        self.assertEqual(item["location"], "石牌校园教育科学学院125会议厅")
        self.assertEqual(item["status"], "active")
        self.assertEqual(item["category"], "lecture")
        self.assertIsNone(item["deadline_at"])
        self.assertTrue(item["schedule_ready"])
        self.assertFalse(item["needs_review"], item["review_reasons"])
        self.assertIn("不能取消报名", "".join(item["requirements"]))

    def test_real_expanded_forward_body(self):
        self.assert_real_forward(REAL_FORWARD_BODY)

    def test_real_expanded_forward_flat_accessibility_body(self):
        self.assert_real_forward(" ".join(REAL_FORWARD_BODY.splitlines()))

    def test_reschedule_multiple_dates_requires_review(self):
        item, = analyze_message(message("《测试》讲座由2026年10月17日14:30改至2026年10月18日15:30，地点：A楼"))
        self.assertEqual(item["category"], "lecture_update")
        self.assertTrue(item["needs_review"])
        self.assertIsNone(item["start_at"])

    def test_weekday_mismatch_requires_review(self):
        item, = analyze_message(message(SAMPLE.replace("周六", "周日")))
        self.assertTrue(any("星期不一致" in text for text in item["review_reasons"]))

    def test_afternoon_shorthand_and_chinese_arrival(self):
        item, = analyze_message(message(SAMPLE.replace("14:30-16:30", "下午2:30-4:30").replace("提前15分钟", "提前十五分钟")))
        self.assertEqual(item["start_at"], "2026-10-17T14:30:00+08:00")
        self.assertEqual(item["end_at"], "2026-10-17T16:30:00+08:00")
        self.assertEqual(item["arrival_at"], "2026-10-17T14:15:00+08:00")

    def test_cross_midnight_not_invented(self):
        item, = analyze_message(message(SAMPLE.replace("14:30-16:30", "23:30-00:30")))
        self.assertIsNone(item["end_at"])
        self.assertTrue(item["needs_review"])

    def test_contract_has_all_fields(self):
        item, = analyze_message(message())
        self.assertEqual(set(item), {"title", "category", "start_at", "end_at", "arrival_at", "location", "speakers", "deadline_at", "requirements", "confidence", "needs_review", "schedule_ready", "review_reasons", "evidence", "source_message_id", "source_group", "source_sender", "source_sent_at", "source_text", "status"})
        self.assertGreaterEqual(item["confidence"], 0)
        self.assertLessEqual(item["confidence"], 1)

    def test_non_dict_ignored(self):
        self.assertEqual(analyze_message(None), [])


if __name__ == "__main__":
    unittest.main()
