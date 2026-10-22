"""Install the generic product outside the checkout and validate shipped UI/runtime."""

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
SMOKE = r"""
from pathlib import Path
from contextlib import ExitStack,redirect_stdout
from unittest.mock import patch
import http.client,io,json,re,sys,threading,time
import wechat_monitor
from wechat_monitor.cli import main
from wechat_monitor.application import Application
from wechat_monitor.web import MonitorServer
assert Path(wechat_monitor.__file__).is_relative_to(Path(sys.prefix))
assert wechat_monitor.__version__==sys.argv[1]
root=Path.cwd()/'workspace'
with ExitStack() as guard:
 guard.enter_context(patch.object(Path,'home',return_value=Path.cwd()/'isolated-home'))
 for target in ('smtplib.SMTP','smtplib.SMTP_SSL','urllib.request.build_opener','wechat_tracker.capture._run_uia','wechat_tracker.macos._helper'):
  guard.enter_context(patch(target,side_effect=AssertionError('Real model/desktop/SMTP forbidden')))
 with redirect_stdout(io.StringIO()):
  assert main(['--data-dir',str(root),'demo'])==0
  assert main(['--data-dir',str(root),'demo'])==0
 app=Application(root)
 window=app.windows()[0]
 assert len(app.windows())==1 and window['runtime']['status']=='stopped'
 assert len(app.repo.rules(window['id']))==1
 app.start(window['id'])
 for _ in range(200):
  if app.repo.counts(window['id']).get('matched')==1:break
  time.sleep(.01)
 assert app.repo.counts(window['id'])['matched']==1
 assert app.runtime.stop(window['id'],wait=2)['status']=='stopped'
 server=MonitorServer(('127.0.0.1',0),app)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 def get(path):
  connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
  connection.request('GET',path);response=connection.getresponse();raw=response.read()
  assert response.status==200,(path,response.status)
  connection.close();return raw
 try:
  html=get('/').decode()
  assets=re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html)
  assert len(assets)>=2,html
  for asset in assets:assert get(asset)
  data=json.loads(get('/api/bootstrap'))
  assert data['version']==sys.argv[1] and data['windows'][0]['counts']['matched']==1
  assert json.loads(get('/api/windows/'+window['id']+'/records'))['records'][0]['result']['core']=='local'
  assert len(json.loads(get('/api/windows/'+window['id']+'/rules'))['rules'])==1
 finally:
  server.shutdown();server.server_close();thread.join(3);app.close()
 print(json.dumps(dict(version=wechat_monitor.__version__,installed_from_wheel=True,ui_assets=len(assets),
  persisted_rule=True,offline_capture_and_recognition=True,duplicate_demo=True,workspace_isolated=True,
  real_model_called=False,native_capture_tested=False,smtp_sent=False)))
"""


def safe_names(names):
    for name in names:
        parts = Path(name).parts
        if (
            any(
                x
                in {
                    "state",
                    ".venv",
                    "node_modules",
                    ".env",
                    ".model-key",
                    "model.json",
                    "config.local.toml",
                    "config.toml",
                }
                for x in parts
            )
            or name.endswith((".sqlite3", ".llm-key", ".log", ".credentials"))
            or Path(name).is_absolute()
            or ".." in parts
        ):
            raise ValueError("Private/unsafe file in distribution: " + name)


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version = project["version"]
    stem = project["name"].replace("-", "_") + "-" + version
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/sync-version.py"), "--check"], check=True
    )
    wheel = ROOT / "dist" / f"{stem}-py3-none-any.whl"
    sdist = ROOT / "dist" / f"{stem}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        safe_names(archive.namelist())
        assert any(
            n.startswith("wechat_monitor/resources/dashboard/assets/")
            and n.endswith(".js")
            for n in archive.namelist()
        )
        assert "wechat_monitor/resources/THIRD_PARTY_NOTICES.txt" in archive.namelist()
    with tarfile.open(sdist) as archive:
        safe_names(archive.getnames())
        assert any(
            n.endswith("/frontend/package-lock.json") for n in archive.getnames()
        )
        assert any(
            n.endswith("/scripts/build-monitor-helper.py") for n in archive.getnames()
        )
    with tempfile.TemporaryDirectory(prefix="wechat-monitor-wheel-") as tmp:
        directory = Path(tmp)
        target = directory / "venv"
        venv.EnvBuilder(with_pip=True).create(target)
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("WECHAT_") and k != "PYTHONPATH"
        }
        environment["PYTHONUTF8"] = "1"
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel),
            ],
            cwd=directory,
            env=environment,
            check=True,
        )
        subprocess.run(
            [str(python), "-c", SMOKE, version],
            cwd=directory,
            env=environment,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(directory / "rebuilt"),
                str(sdist),
            ],
            env=environment,
            check=True,
        )
    print(
        json.dumps(
            dict(distribution_files_checked=True, sdist_rebuild=True, wheel=str(wheel))
        )
    )


if __name__ == "__main__":
    main()
