"""Local-only SMTP credentials: Windows DPAPI, macOS Keychain helper."""
import base64
import ctypes
from getpass import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .notify import _mailboxes, send_email


_MAC_CREDENTIAL_CACHE = {}
_MAC_CREDENTIAL_LOCK = threading.RLock()


def credential_status(config):
    """Describe setup without decrypting credentials or invoking native helpers."""
    source = config
    linked = config.get("email_credentials_config_path")
    if linked:
        from .config import load_config
        path = Path(linked).expanduser()
        if not path.is_absolute():
            path = Path(config["config_path"]).parent / path
        source = load_config(path)
        if source.get("email_credentials_config_path"):
            raise ValueError("邮箱凭据来源不支持多层引用")
    email = source.get("email", {})
    fields = ("smtp_user_env", "smtp_password_env", "from_env", "to_env")
    environment_ready = all(email.get(key) and os.environ.get(email[key]) for key in fields)
    backend = {"darwin": "macos_keychain", "win32": "windows_dpapi"}.get(sys.platform, "environment")
    marker = {"darwin": "email.keychain", "win32": "email.credentials"}.get(sys.platform)
    configured = bool(environment_ready or (marker and (Path(source["data_dir"]) / marker).is_file()))
    return dict(configured=configured, backend=backend, settings_shared=bool(linked),
                setup_config_path=str(source.get("config_path", "")), source_group=source.get("group", ""),
                environment_variables=[email.get(field, "") for field in fields])


def _credential_helper(config):
    capture = Path(config.get("capture", {}).get("mac_helper", ""))
    if capture.parent.name == "MacOS" and capture.parent.parent.name == "Contents":
        app = capture.parent.parent.parent
        stable = app.with_name("WeChat Lecture Tracker Credentials.app") / "Contents/MacOS/wechat-credentials"
        if stable.is_file():
            return stable
    return capture


def _cached_mac_credentials(config, marker):
    # Cache only in this process's memory. A saved/updated marker invalidates
    # both successful reads and a cancelled/timed-out authorization attempt.
    stat = marker.stat()
    key = (os.getpid(), str(marker.resolve()), config["group"])
    version = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    with _MAC_CREDENTIAL_LOCK:
        previous = _MAC_CREDENTIAL_CACHE.get(key)
        if previous is not None and previous[0] == version:
            if previous[2]:
                raise RuntimeError(previous[2])
            return dict(previous[1])
        try:
            values = _keychain(config, "credential-get")
            fields = ("smtp_user_env", "smtp_password_env", "from_env", "to_env")
            if not isinstance(values, dict) or any(not isinstance(values.get(field), str) or not values[field] for field in fields):
                raise RuntimeError("macOS Keychain credential data is incomplete")
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            message = "邮箱钥匙串授权未完成；后台不会反复弹窗，请在配置与邮箱设置中点击“授权邮箱”，完成后会自动重试"
            _MAC_CREDENTIAL_CACHE[key] = (version, None, message)
            raise RuntimeError(message) from None
        _MAC_CREDENTIAL_CACHE[key] = (version, dict(values), None)
        return dict(values)


def _dpapi(data, *, decrypt=False):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(incoming), "WeChat lecture SMTP", None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise RuntimeError("The local account could not access its encrypted SMTP credentials")
    try:
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        kernel.LocalFree(outgoing.data)


def _keychain(config, action, value=None):
    helper = _credential_helper(config)
    if not helper.is_file():
        raise RuntimeError("Build the macOS helper before configuring Keychain credentials")
    request = dict(service="wechat-lecture-tracker", account=config["group"])
    if value is not None:
        request["value"] = json.dumps(value)
    # Different group services may share the same secure source. Serialize
    # authorization across processes so system password panels cannot stack.
    import fcntl
    lock_path = Path(config["data_dir"]) / ".keychain-access.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = subprocess.run([str(helper), action], input=json.dumps(request).encode(), capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise RuntimeError("macOS Keychain authorization timed out") from None
    if result.returncode:
        raise RuntimeError("macOS Keychain access failed; check the local account and Keychain permission")
    return json.loads(result.stdout) if action == "credential-get" else None


def save_credentials(config, values):
    if sys.platform == "win32":
        target = Path(config["data_dir"]) / "email.credentials"
        target.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _dpapi(json.dumps(values).encode("utf-8"))
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    elif sys.platform == "darwin":
        _keychain(config, "credential-set", values)
        marker = Path(config["data_dir"]) / "email.keychain"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("Credentials are stored in the current macOS user's Keychain.\n", encoding="utf-8")
        marker.chmod(0o600)
    else:
        raise RuntimeError("Secure credential storage requires Windows or macOS")


def authorize_credentials(config):
    """Explicit user action; the native helper discards secrets before replying."""
    linked = config.get("email_credentials_config_path")
    if linked:
        from .config import load_config
        path = Path(linked).expanduser()
        if not path.is_absolute():
            path = Path(config["config_path"]).parent / path
        source = load_config(path)
        if source.get("email_credentials_config_path"):
            raise RuntimeError("邮箱凭据来源不支持多层引用")
        return authorize_credentials(source)
    if sys.platform != "darwin":
        return {"configured": apply_credentials(config), "smtp_sent": False}
    marker = Path(config["data_dir"]) / "email.keychain"
    if not marker.is_file():
        if apply_credentials(config):
            return {"configured": True, "smtp_sent": False}
        raise ValueError("邮箱尚未配置，请先配置邮箱")
    if _credential_helper(config).name != "wechat-credentials":
        raise RuntimeError("请先安装独立邮箱授权助手")
    _keychain(config, "credential-authorize")
    # Wake the running service's cached failure without exposing values to the
    # web server or writing any password to disk.
    previous = marker.stat()
    changed_at = max(time.time_ns(), previous.st_mtime_ns + 1_000_000)
    os.utime(marker, ns=(previous.st_atime_ns, changed_at))
    with _MAC_CREDENTIAL_LOCK:
        _MAC_CREDENTIAL_CACHE.pop((os.getpid(), str(marker.resolve()), config["group"]), None)
    return {"configured": True, "smtp_sent": False}


def apply_credentials(config):
    email = config.get("email", {})
    fields = ("smtp_user_env", "smtp_password_env", "from_env", "to_env")
    linked = config.get("email_credentials_config_path")
    if linked:
        from .config import load_config
        source_path = Path(linked).expanduser()
        if not source_path.is_absolute():
            source_path = Path(config["config_path"]).parent / source_path
        source = load_config(source_path)
        if source.get("email_credentials_config_path"):
            raise RuntimeError("邮箱凭据来源不支持多层引用")
        if not apply_credentials(source):
            return False
        # Reuse secure storage, without copying passwords to config or disk.
        for field in fields:
            os.environ[email[field]] = os.environ[source["email"][field]]
        return True
    if sys.platform == "win32":
        path = Path(config["data_dir"]) / "email.credentials"
        if path.is_file():
            values = json.loads(_dpapi(path.read_bytes(), decrypt=True))
            for field in fields:
                if field not in values or not isinstance(values[field], str):
                    raise RuntimeError("Encrypted email credential data is incomplete")
                os.environ[email[field]] = values[field]
    elif sys.platform == "darwin" and (Path(config["data_dir"]) / "email.keychain").is_file():
        values = _cached_mac_credentials(config, Path(config["data_dir"]) / "email.keychain")
        for field in fields:
            os.environ[email[field]] = values[field]
    return all(isinstance(email.get(field), str) and bool(os.environ.get(email[field])) for field in fields)


def configure_email(config):
    print("QQ邮箱配置只保存在本机加密存储，不写入Git或聊天。")
    print("输入后会向下述收件邮箱发送一封测试邮件；成功后保存，服务自动加载。")
    user = input("发件QQ邮箱地址: ").strip()
    sender = _mailboxes(user, "发件邮箱", multiple=False)[0]
    recipient = input("收件邮箱地址（直接回车使用同一邮箱）: ").strip() or sender
    _mailboxes(recipient, "收件邮箱", multiple=True)
    password = getpass("QQ邮箱SMTP授权码（不是QQ登录密码，输入隐藏）: ").replace(" ", "").strip()
    if not password:
        raise ValueError("SMTP授权码不能为空")
    values = dict(smtp_user_env=sender, smtp_password_env=password, from_env=sender, to_env=recipient)
    old_values = {}
    for field, value in values.items():
        key = config["email"][field]
        old_values[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        send_email("微信讲座监控配置测试", "这是一封本机服务配置测试邮件。收到后，服务可按配置发送讲座通知。", config["email"])
        save_credentials(config, values)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"configured": True, "test_email_sent": True, "secret_storage": "DPAPI" if sys.platform == "win32" else "macOS Keychain"}
