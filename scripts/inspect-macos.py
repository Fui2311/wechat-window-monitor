"""Read target-group capture and service health without driving the desktop UI."""
import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wechat_tracker.capture import CaptureError, capture_once, probe
from wechat_tracker.config import load_config
from wechat_tracker.service import read_status


SERVICE_FIELDS = (
    'running', 'status', 'pid', 'worker_pid', 'worker_restarts', 'snapshots',
    'observed_messages', 'events_created', 'last_capture_at', 'last_error',
    'capture_gaps', 'send_enabled', 'send_ready', 'email_state',
    'email_sent', 'email_failures', 'coverage',
)
PROBE_FIELDS = (
    'ok', 'status', 'source', 'group', 'bundle_id', 'process_id',
    'window_title', 'list_count', 'list_role', 'message_count', 'observed_at',
)


def inspect(config, *, include_messages=False):
    if config.get('capture', {}).get('source') != 'macos_ax':
        raise ValueError('This diagnostic requires capture.source = "macos_ax"')
    service = read_status(config)
    report = {
        'group': config['group'],
        'service': {key: service[key] for key in SERVICE_FIELDS if key in service},
        'capture_scope': 'loaded rows in the exact target-group window',
        'history_complete': False,
        'ui_actions_performed': False,
        'smtp_sent': False,
        'capture_policy': {
            'scope': 'configuration file; restart service to apply changes',
            'follow_tail': config['capture'].get('follow_tail', True),
            'auto_expand_forward': config['capture'].get('auto_expand_forward', False),
            'include_existing': config.get('service', {}).get('include_existing', True),
        },
    }
    result = probe(config)
    report['probe'] = {key: result[key] for key in PROBE_FIELDS if key in result}
    if result.get('ok'):
        # Diagnostics remain passive even if normal monitoring follows the tail.
        passive = dict(config, capture=dict(config.get('capture', {}), follow_tail=False, recover_window=False))
        messages = capture_once(passive)
        report['capture'] = {
            'messages': len(messages),
            'kinds': dict(Counter(message['kind'] for message in messages)),
            'pending_expansion': sum(bool(message.get('source_meta', {}).get('pending_expansion'))
                                     for message in messages),
            'original_time_unknown': sum(message.get('sent_at') is None for message in messages),
        }
        if include_messages:
            report['messages'] = [{key: message.get(key) for key in (
                'id', 'kind', 'text', 'sent_at', 'observed_at')}
                for message in messages]
    database = Path(config['data_dir']) / 'tracker.sqlite3'
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            report['database'] = {
                'read_only': True,
                'integrity': db.execute('PRAGMA integrity_check').fetchone()[0],
                'counts': {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                           for name in ('messages', 'events', 'outbox', 'capture_seen')},
            }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path.home() / 'Library/Application Support/WeChatLectureTracker/config.toml'))
    parser.add_argument('--messages', action='store_true', help='include exact target-group message text; default prints counts only')
    args = parser.parse_args()
    try:
        report = inspect(load_config(args.config), include_messages=args.messages)
    except (CaptureError, OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        # No configuration dumps, environment values, socket tokens or SMTP responses.
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['probe'].get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
