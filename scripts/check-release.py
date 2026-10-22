"""Install a built wheel outside the repository and verify the shipped product.

Only synthetic fixtures and localhost HTTP are used. Desktop, SMTP and model
calls are forbidden. This is intentionally independent of editable installs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parent.parent

SMOKE = r'''
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import threading
from contextlib import redirect_stdout
from unittest.mock import patch

import wechat_tracker
from wechat_tracker.cli import main
from wechat_tracker.paths import RESOURCE_ROOT
from wechat_tracker.web import WebControlHandler, WebControlServer

assert Path(wechat_tracker.__file__).is_relative_to(Path(sys.prefix)), wechat_tracker.__file__
for name in ('mac-capture.swift', 'mac-credentials.swift', 'probe-weixin.ps1', 'stream-weixin.ps1',
             'expand-weixin-forward.ps1', 'screenshot.jsonl', 'config.windows.toml', 'config.macos.toml',
             'web/index.html', 'web/app.js', 'web/app.css'):
    assert (RESOURCE_ROOT / name).is_file(), name
assert wechat_tracker.__version__ == sys.argv[1]
config = Path('demo.local.toml').resolve()
def run(args):
    with redirect_stdout(io.StringIO()):
        assert main(args) == 0, args
with patch('smtplib.SMTP', side_effect=AssertionError('SMTP forbidden')), \
     patch('smtplib.SMTP_SSL', side_effect=AssertionError('SMTP forbidden')), \
     patch('urllib.request.build_opener', side_effect=AssertionError('LLM forbidden')), \
     patch('wechat_tracker.capture._run_uia', side_effect=AssertionError('desktop forbidden')):
    run(['init', str(config), '--platform', 'jsonl'])
    run(['--config', str(config), 'demo'])
    run(['--config', str(config), 'demo'])
    run(['--config', str(config), 'ingest', str(RESOURCE_ROOT / 'screenshot.jsonl')])
    run(['--config', str(config), 'ingest', str(RESOURCE_ROOT / 'screenshot.jsonl')])
    run(['--config', str(config), 'tick'])
    run(['--config', str(config), 'calendar', 'state/lectures.ics'])
assert Path('state/demo-email-preview.txt').read_text(encoding='utf-8')
assert 'BEGIN:VEVENT' in Path('state/lectures.ics').read_text(encoding='utf-8')
with sqlite3.connect('state/tracker.sqlite3') as connection:
    assert connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1
    assert connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0] == 1
server = WebControlServer(('127.0.0.1', 0), WebControlHandler, config)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
paths = ['/', '/app.js', '/app.css', '/api/status', '/api/events', '/api/messages', '/api/outbox',
         '/api/processing', '/api/preview_email?job_id=1', '/api/calendar/export']
try:
    for path in paths:
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        client.request('GET', path)
        response = client.getresponse()
        payload = response.read()
        assert response.status == 200, (path, response.status, payload)
        if path.startswith('/api/') and path != '/api/calendar/export':
            assert json.loads(payload)['ok'] is True, path
        client.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
print(json.dumps(dict(version=wechat_tracker.__version__, installed_from_wheel=True,
    duplicate_replay=True, preview_only=True, calendar=True, http_endpoints=len(paths),
    native_capture_tested=False, smtp_sent=False)))
'''


def check_contents(names):
    forbidden = ('.sqlite3', '.llm-key', '.credentials', '.keychain', '.log')
    for name in names:
        parts = Path(name).parts
        if (any(part in {'state', '.venv', '.env', 'config.local.toml', 'config.toml'} for part in parts)
                or name.endswith(forbidden) or Path(name).is_absolute() or '..' in parts):
            raise RuntimeError('Local/private or unsafe file in distribution: ' + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    wheel = args.wheel or ROOT / 'dist' / f'wechat_lecture_tracker-{version}-py3-none-any.whl'
    with zipfile.ZipFile(wheel) as archive:
        check_contents(archive.namelist())
    sdist = ROOT / 'dist' / f'wechat_lecture_tracker-{version}.tar.gz'
    with tarfile.open(sdist) as archive:
        check_contents(archive.getnames())
    with tempfile.TemporaryDirectory(prefix='wechat-wheel-smoke-') as directory:
        folder = Path(directory)
        environment = {k: v for k, v in os.environ.items() if not k.startswith('WECHAT_') and k != 'PYTHONPATH'}
        environment['PYTHONUTF8'] = '1'
        target = folder / 'venv'
        venv.EnvBuilder(with_pip=True).create(target)
        python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel.resolve())],
                       cwd=folder, env=environment, check=True)
        subprocess.run([str(python), '-c', SMOKE, version], cwd=folder, env=environment, check=True)
        # Rebuild the shipped sdist to catch missing build inputs and resources.
        subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                        '--wheel-dir', str(folder / 'rebuilt'), str(sdist)], env=environment, check=True)
    print(json.dumps(dict(distribution_files_checked=True, sdist_rebuild=True, wheel=str(wheel))))


if __name__ == '__main__':
    main()
