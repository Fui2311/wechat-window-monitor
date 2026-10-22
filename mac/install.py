"""Native, offline-from-source installer. Run with macOS Python 3.11+."""
import json
import hashlib
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import venv

ROOT = Path(__file__).resolve().parent.parent
BASE = Path.home() / "Library/Application Support/WeChatLectureTracker"
APP = Path.home() / "Applications/WeChat Lecture Tracker Capture.app"


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def install_credential_helper():
    """Keep the Keychain executable unchanged across capture-only updates."""
    app = APP.with_name("WeChat Lecture Tracker Credentials.app")
    source = ROOT / "wechat_tracker/resources/mac-credentials.swift"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    info_path = app / "Contents/Info.plist"
    if info_path.is_file():
        info = plistlib.loads(info_path.read_bytes())
        if info.get("CredentialSourceSHA256") == digest:
            valid = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], capture_output=True)
            if valid.returncode == 0:
                return app
    APP.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wechat-credentials-", dir=APP.parent) as directory:
        staged = Path(directory) / app.name
        executable = staged / "Contents/MacOS/wechat-credentials"
        executable.parent.mkdir(parents=True)
        run(["/usr/bin/xcrun", "swiftc", "-O", "-swift-version", "5", source, "-o", executable])
        info = dict(CFBundleIdentifier="local.wechat-lecture-tracker.credentials",
                    CFBundleName="WeChat Lecture Tracker Credentials", CFBundleExecutable="wechat-credentials",
                    CFBundlePackageType="APPL", CFBundleVersion="1", LSUIElement=True,
                    CredentialSourceSHA256=digest)
        (staged / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", info["CFBundleIdentifier"], staged], capture_output=True)
        run(["/usr/bin/codesign", "--verify", "--strict", staged], capture_output=True)
        if app.exists():
            app.rename(app.with_name(app.stem + ".backup-" + str(time.time_ns()) + ".app"))
        shutil.move(str(staged), str(app))
    return app


def main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"] != "wechat-lecture-tracker":
        raise SystemExit("This legacy installer does not deploy Window Monitor. Use pip install . and scripts/build-monitor-helper.py; existing lecture installation was not changed.")
    if sys.platform != "darwin" or sys.version_info < (3, 11):
        raise SystemExit("Requires a native macOS Python 3.11+ installation")
    silicon = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True)
    if silicon.stdout.strip() == "1" and platform.machine() != "arm64":
        raise SystemExit("Use an arm64 Python on Apple Silicon; this Python is running under Rosetta")
    # Validate the archive before any changes to an existing installation.
    run([sys.executable, ROOT / "mac/verify-package.py", ROOT])
    run(["/usr/bin/xcrun", "--find", "swiftc"], stdout=subprocess.DEVNULL)
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    APP.parent.mkdir(parents=True, exist_ok=True)
    install_credential_helper()
    config = BASE / "config.toml"
    python = BASE / ".venv/bin/python"
    # Compile before stopping a working installation; failed compilation is harmless.
    with tempfile.TemporaryDirectory(prefix="wechat-capture-", dir=APP.parent) as staging:
        app = Path(staging) / APP.name
        contents = app / "Contents"
        executable = contents / "MacOS/wechat-capture"
        executable.parent.mkdir(parents=True)
        run(["/usr/bin/xcrun", "swiftc", "-O", "-swift-version", "5",
             ROOT / "wechat_tracker/resources/mac-capture.swift", "-o", executable])
        info = dict(CFBundleIdentifier="local.wechat-lecture-tracker.capture",
                    CFBundleName="WeChat Lecture Tracker Capture", CFBundleExecutable="wechat-capture",
                    CFBundlePackageType="APPL", CFBundleShortVersionString=tomllib.loads(
                        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
                    CFBundleVersion="1", LSUIElement=True, LSMinimumSystemVersion="12.0")
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        run(["/usr/bin/codesign", "--force", "--sign", "-", "--identifier", info["CFBundleIdentifier"], app])
        run(["/usr/bin/codesign", "--verify", "--strict", app])
        if config.exists() and python.exists():
            run([python, "-m", "wechat_tracker", "--config", config, "service", "stop"])
        if APP.exists():
            # Keep a recoverable prior app rather than recursively deleting it.
            backup = APP.with_name(APP.stem + ".backup-" + str(time.time_ns()) + ".app")
            APP.rename(backup)
            print("Previous capture app saved:", backup)
        shutil.move(str(app), str(APP))
    if not python.exists():
        venv.EnvBuilder(with_pip=False).create(BASE / ".venv")
    site = run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
               capture_output=True, text=True).stdout.strip()
    destination = Path(site) / "wechat_tracker"
    if destination.exists():
        destination.rename(destination.with_name("wechat_tracker.backup-" + str(time.time_ns())))
    shutil.copytree(ROOT / "wechat_tracker", destination,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.ps1"))
    # Copy only portable diagnostics and synthetic fixtures, never local state.
    for name in ("mac", "examples"):
        shutil.copytree(ROOT / name, BASE / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    helper = APP / "Contents/MacOS/wechat-capture"
    if not config.exists():
        template = (ROOT / "mac/config.example.toml").read_text(encoding="utf-8")
        config.write_text(template.replace('"__MAC_HELPER__"', json.dumps(str(helper), ensure_ascii=False)), encoding="utf-8")
        config.chmod(0o600)
    else:
        print("Existing config preserved:", config)
        print("Capture helper must point to:", helper)
    for path in (BASE / "mac").glob("*.command"):
        path.chmod(0o755)
    print("Installed:", BASE)
    print("Next: open the capture app, allow Accessibility, open the exact group window, then run:")
    print('bash "' + str(BASE / "mac/manage.command") + '" verify')
    print('bash "' + str(BASE / "mac/manage.command") + '" install')
    print("QQ email setup can be performed later. No email is sent by this installer.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("Installation failed: " + str(error) + "\nIf Swift is missing, run xcode-select --install and retry.")
