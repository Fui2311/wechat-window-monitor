import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from wechat_tracker.config import load_config, update_toml_root_keys
from wechat_tracker.engine import Engine
from wechat_tracker.processing import (ProcessingError, extract_llm, key_path, process_message,
                                       public_settings, save_settings, validate_settings)
from wechat_tracker.store import Store
from wechat_tracker.web import WebControlServer, WebControlHandler


class ModelHandler(BaseHTTPRequestHandler):
    reply = {"items": [{"index": 0, "is_lecture": True, "title": "模型提取标题", "summary": "内容摘要"}]}
    calls = []
    status = 200

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append((self.path, self.headers.get('Authorization'), body))
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        result = {"choices": [{"message": {"content": json.dumps(self.reply)}}]}
        self.wfile.write(json.dumps(result).encode())


class ProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
        threading.Thread(target=cls.model.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.model.shutdown(); cls.model.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config.toml'
        self.path.write_text('group = "测试群"\ndata_dir = "state"\n# preserve me\n[email]\nsmtp_host = "smtp.example.com"\n')
        self.config = load_config(self.path)
        self.llm = dict(processing_mode='llm', llm_model='test-model',
                        llm_base_url=f'http://127.0.0.1:{self.model.server_port}/v1')
        ModelHandler.calls = []
        ModelHandler.status = 200
        ModelHandler.reply = {"items": [{"index": 0, "is_lecture": True, "title": "模型提取标题", "summary": "内容摘要"}]}
        self.message = dict(id='m1', group='测试群', text='欢迎参加前沿技术交流，报名链接 https://example.com/signup')

    def test_real_http_extraction_and_auth(self):
        events = extract_llm(self.message, dict(self.config, **self.llm), key='test-only-key')
        self.assertEqual(events[0]['title'], '模型提取标题')
        self.assertEqual(events[0]['source_text'], self.message['text'])
        path, auth, body = ModelHandler.calls[0]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(auth, 'Bearer test-only-key')
        self.assertEqual(body['model'], 'test-model')
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertNotIn('enable_thinking', body)
        self.assertNotIn('测试群', body['messages'][1]['content'])

    def test_disable_thinking_sent_at_top_level(self):
        extract_llm(self.message, dict(self.config, **self.llm, llm_disable_thinking=True))
        body = ModelHandler.calls[0][2]
        self.assertIs(body['enable_thinking'], False)
        self.assertNotIn('extra_body', body)

    def test_thinking_setting_persists_and_hot_loads(self):
        updated = save_settings(self.config, dict(self.llm, llm_disable_thinking=True))
        self.assertIs(updated['llm_disable_thinking'], True)
        self.assertIs(public_settings(updated)['llm_disable_thinking'], True)
        process_message(self.message, self.config)
        self.assertIs(ModelHandler.calls[-1][2]['enable_thinking'], False)
        save_settings(updated, {'llm_disable_thinking': False})
        process_message(dict(self.message, id='m2'), self.config)
        self.assertNotIn('enable_thinking', ModelHandler.calls[-1][2])

    def test_rules_never_use_network(self):
        self.assertEqual(process_message(self.message, self.config), [])
        self.assertEqual(ModelHandler.calls, [])

    def test_llm_failure_falls_back_without_losing_raw(self):
        save_settings(self.config, self.llm)
        ModelHandler.status = 401
        message = dict(self.message, text='讲座时间待定')
        event, = process_message(message, self.config)
        self.assertEqual(event['processing_method'], 'rules_fallback')
        self.assertEqual(event['source_text'], '讲座时间待定')
        self.assertEqual(event['status'], 'noticed')

    def test_invalid_json_structure_falls_back(self):
        save_settings(self.config, self.llm)
        ModelHandler.reply = {'items': []}
        event, = process_message(dict(self.message, text='学术报告'), self.config)
        self.assertEqual(event['processing_method'], 'rules_fallback')

    def test_negative_model_never_suppresses_local_positive(self):
        ModelHandler.reply = {'items': [{'index': 0, 'is_lecture': False}]}
        event, = extract_llm(dict(self.message, text='讲座取消了'), dict(self.config, **self.llm))
        self.assertEqual(event['status'], 'noticed')

    def test_nonlecture_negative_is_empty(self):
        ModelHandler.reply = {'items': [{'index': 0, 'is_lecture': False}]}
        self.assertEqual(extract_llm(self.message, dict(self.config, **self.llm)), [])

    def test_hallucinated_links_and_dates_not_accepted(self):
        ModelHandler.reply['items'][0].update(signup_url='https://evil.example', start_at='2099-01-01T10:00:00+08:00',
            start_evidence='不存在原文', activity_time_text='不存在原文', signup_start_text='编造报名时间')
        event, = extract_llm(self.message, dict(self.config, **self.llm))
        self.assertEqual(event['signup_url'], 'https://example.com/signup')
        self.assertIsNone(event['start_at'])
        self.assertEqual(event['activity_time_text'], '')
        self.assertEqual(event['signup_start_text'], '')

    def test_literal_signup_link_and_time_excerpt_are_used(self):
        text = '讲座时间：2026年10月20日 10:00\n报名开始时间：2026年10月19日 09:00\n报名链接：https://example.com/signup'
        ModelHandler.reply['items'][0].update(signup_url='https://example.com/signup',
            activity_time_text='2026年10月20日 10:00', start_at='2026-10-20T10:00:00+08:00',
            start_evidence='2026年10月20日 10:00')
        event, = extract_llm(dict(self.message, text=text), dict(self.config, **self.llm))
        self.assertEqual(event['start_at'], '2026-10-20T10:00:00+08:00')
        self.assertEqual(event['signup_url'], 'https://example.com/signup')

    def test_forward_batch_keeps_child_source_identity(self):
        ModelHandler.reply = {'items': [{'index': 0, 'is_lecture': True}, {'index': 1, 'is_lecture': False}]}
        message = dict(self.message, kind='forward', children=[{'text': '学术报告'}, {'text': '收到'}])
        event, = extract_llm(message, dict(self.config, **self.llm))
        self.assertEqual(event['source_message_id'], 'm1/child-1')
        self.assertIsNone(event['source_sent_at'])
        self.assertEqual(len(ModelHandler.calls), 1)

    def test_file_does_not_get_model_invented_body(self):
        event, = extract_llm(dict(self.message, kind='file', text='文件\n讲座名单.pdf'), dict(self.config, **self.llm))
        self.assertEqual(event['title'], '讲座名单.pdf')
        self.assertNotEqual(event['summary'], '内容摘要')
        self.assertIn('未读取', event['evidence']['content_note'])

    def test_engine_hot_switch_and_dedup_no_repeated_call(self):
        store = Store(Path(self.temp.name) / 'test.sqlite3'); self.addCleanup(store.close)
        engine = Engine(store, self.config)
        self.assertEqual(engine.ingest(self.message)['events'], [])
        save_settings(self.config, self.llm)
        event, = engine.ingest(dict(self.message, id='m2'))['events']
        self.assertEqual(event['processing_method'], 'llm')
        self.assertTrue(engine.ingest(dict(self.message, id='m2'))['duplicate'])
        self.assertEqual(len(ModelHandler.calls), 1)
        save_settings(load_config(self.path), {'processing_mode': 'rules'})
        engine.ingest(dict(self.message, id='m3'))
        self.assertEqual(len(ModelHandler.calls), 1)
        self.assertEqual(len(store.jobs()), 1)
        self.assertIn('LLM 提取信息', engine.dispatch(send=False)[0]['body'])
        self.assertTrue(engine.dispatch(send=False)[0]['body'].endswith(self.message['text']))

    def test_switch_updates_new_mail_format_and_preview_matches_dispatch(self):
        store = Store(Path(self.config['data_dir']) / 'tracker.sqlite3')
        self.addCleanup(store.close)
        engine = Engine(store, self.config)
        raw = dict(self.message, text='讲座报名开放了')
        engine.ingest(dict(raw, id='rules-1'))
        save_settings(self.config, self.llm)
        engine.ingest(dict(raw, id='llm-2'))
        save_settings(load_config(self.path), {'processing_mode': 'rules'})
        engine.ingest(dict(raw, id='rules-3'))
        save_settings(load_config(self.path), self.llm)
        ModelHandler.status = 503
        engine.ingest(dict(raw, id='fallback-4'))
        jobs = store.jobs()
        self.assertEqual([json.loads(job['payload'])['mail_format'] for job in jobs],
                         ['source', 'llm_summary', 'source', 'source'])
        base = self.start_web()
        previews = [self.request(base+'/api/preview_email?job_id='+str(job['id'])) for job in jobs]
        self.assertEqual([p['mail_format'] for p in previews], ['source', 'llm_summary', 'source', 'source'])
        self.assertEqual(previews[0]['body'], raw['text'])
        self.assertIn('内容摘要', previews[1]['body'])
        self.assertIn('报名开始时间', previews[1]['body'])
        self.assertEqual(previews[2]['body'], raw['text'])
        self.assertIn('LLM 提取失败', previews[3]['body'])
        # SMTP boundary is mocked: compare actual dispatch arguments to the API preview.
        with patch('wechat_tracker.engine.send_email') as send:
            result = engine.dispatch(send=True)
        self.assertEqual(len(result), 4)
        for call, preview in zip(send.call_args_list, previews):
            self.assertEqual(call.args[:2], (preview['subject'], preview['body']))
            self.assertEqual(call.kwargs['html_body'], preview['html_body'])

    def test_linked_service_uses_persistent_shared_key_and_mode_for_every_message(self):
        save_settings(self.config, dict(self.llm, api_key='shared-test-key', llm_disable_thinking=True))
        linked_path = Path(self.temp.name) / 'test-service.toml'
        linked_path.write_text('group="邮件测试"\ndata_dir="test-data"\nprocessing_config_path='+json.dumps(str(self.path))+'\n')
        linked = load_config(linked_path)
        store = Store(Path(linked['data_dir']) / 'tracker.sqlite3')
        self.addCleanup(store.close)
        engine = Engine(store, linked)
        for index in range(2):
            event, = engine.ingest(dict(self.message, id=str(index), group='邮件测试'))['events']
            self.assertEqual(event['source_group'], '邮件测试')
            self.assertEqual(event['processing_method'], 'llm')
            self.assertEqual(event['mail_format'], 'llm_summary')
        self.assertTrue(all(call[1]=='Bearer shared-test-key' for call in ModelHandler.calls))
        self.assertTrue(all(call[2]['enable_thinking'] is False for call in ModelHandler.calls))
        self.assertFalse(key_path(linked).exists())
        self.assertTrue(public_settings(linked)['api_key_configured'])
        save_settings(load_config(self.path), {'processing_mode':'rules'})
        event, = engine.ingest(dict(self.message, id='rules-3', group='邮件测试', text='讲座报名开放'))['events']
        self.assertEqual(event['processing_method'], 'rules')
        self.assertEqual(len(ModelHandler.calls), 2)
        self.assertTrue(engine.last_processing_report['processing_settings_shared'])

    def test_failed_llm_without_lecture_clue_still_reports_error(self):
        save_settings(self.config, self.llm)
        ModelHandler.status = 401
        report = {}
        process_message(dict(self.message, text='普通通知'), self.config, report=report)
        self.assertEqual(report['processing_last_method'], 'rules_fallback')
        self.assertIn('401', report['processing_last_error'])
        self.assertIn('processing_last_at', report)

    def test_discovered_live_service_database_is_listed_and_readable(self):
        other_temp = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp.cleanup)
        directory = Path(other_temp.name) / 'separate-service'
        store = Store(directory / 'tracker.sqlite3')
        event = dict(id='test-notice', title='测试讲座', category='lecture_notice', source_group='邮件测试')
        store.db.execute('INSERT INTO events VALUES (?,?,?,?)', ('test-notice','测试讲座','邮件测试',json.dumps(event)))
        store.db.commit(); store.close()
        service = dict(data_dir=str(directory), group='邮件测试', running=True, is_current=False)
        patcher = patch('wechat_tracker.console.handler._list_services', return_value=[service])
        patcher.start(); self.addCleanup(patcher.stop)
        base = self.start_web()
        databases = self.request(base+'/api/databases')['databases']
        found = next(db for db in databases if db['path']==str((directory/'tracker.sqlite3').resolve()))
        self.assertTrue(found['is_live_service'])
        self.assertEqual(found['event_count'], 1)
        from urllib.parse import urlencode
        events = self.request(base+'/api/events?'+urlencode({'db':found['path']}))['events']
        self.assertEqual(events[0]['title'], '测试讲座')

    def test_key_separate_private_and_not_returned(self):
        config = save_settings(self.config, dict(self.llm, api_key='private-test-key'))
        if os.name != "nt":
            self.assertEqual(key_path(config).stat().st_mode & 0o777, 0o600)
        self.assertNotIn('private-test-key', self.path.read_text())
        self.assertNotIn('private-test-key', json.dumps(public_settings(config)))
        self.assertTrue(public_settings(config)['api_key_configured'])
        config = save_settings(config, {'processing_mode': 'rules', 'api_key': ''})
        self.assertTrue(key_path(config).is_file())
        config = save_settings(config, {'clear_api_key': True})
        self.assertFalse(key_path(config).exists())
        self.assertIn('# preserve me', self.path.read_text())
        self.assertEqual(config['email']['smtp_host'], 'smtp.example.com')

    def test_failed_config_save_restores_key(self):
        save_settings(self.config, {'api_key': 'old'})
        with patch('wechat_tracker.extraction.settings.update_toml_root_keys', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                save_settings(self.config, {'api_key': 'new'})
        self.assertEqual(key_path(self.config).read_text(), 'old')

    def test_invalid_settings_rejected_before_write(self):
        old = self.path.read_bytes()
        for updates in ({'processing_mode': 'invalid'}, {'llm_timeout_seconds': True},
                        {'llm_disable_thinking': 'false'}, {'llm_disable_thinking': 0},
                        {'llm_timeout_seconds': float('nan')}, {'llm_base_url': 'http://remote.example/v1'},
                        {'llm_base_url': 'https://user:key@example.com/v1'}, {'llm_base_url': 'https://example.com?key=x'},
                        {'api_key': 'bad\nkey'}, {'processing_mode': 'llm'}, {'email': {}}):
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    save_settings(self.config, updates)
                self.assertEqual(self.path.read_bytes(), old)

    def start_web(self):
        server = WebControlServer(('127.0.0.1', 0), WebControlHandler, self.path)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def request(self, url, body=None, headers=None):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                        headers=headers or {'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_web_config_and_test_no_database_no_secret_readback(self):
        base = self.start_web()
        result = self.request(base+'/api/processing', dict(self.llm, api_key='web-test-key', llm_disable_thinking=True))
        self.assertTrue(result['processing']['api_key_configured'])
        self.assertIs(result['processing']['llm_disable_thinking'], True)
        self.assertNotIn('web-test-key', json.dumps(result))
        preview = self.request(base+'/api/processing/test', {'text': self.message['text']})
        self.assertIs(ModelHandler.calls[-1][2]['enable_thinking'], False)
        self.request(base+'/api/processing/test', {'text': self.message['text'], 'llm_disable_thinking': False})
        self.assertNotIn('enable_thinking', ModelHandler.calls[-1][2])
        self.assertIs(self.request(base+'/api/processing')['processing']['llm_disable_thinking'], True)
        self.assertEqual(preview['events'][0]['title'], '模型提取标题')
        self.assertFalse(preview['stored']); self.assertFalse(preview['sent'])
        self.assertFalse((Path(self.config['data_dir'])/'tracker.sqlite3').exists())
        status = self.request(base+'/api/status')
        self.assertEqual(status['config']['processing']['processing_mode'], 'llm')
        self.assertNotIn('web-test-key', json.dumps(status))

    def test_web_error_sanitized_and_origin_enforced(self):
        base = self.start_web()
        self.request(base+'/api/processing', self.llm)
        ModelHandler.status = 401
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(base+'/api/processing/test', {'text': '讲座'})
        self.assertEqual(error.exception.code, 400)
        self.assertIn('401', error.exception.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(base+'/api/processing', {'processing_mode': 'rules'}, {'Origin': 'https://evil.example'})
        self.assertEqual(error.exception.code, 403)
