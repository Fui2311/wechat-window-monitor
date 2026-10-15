"""Local-only SMTP credentials: Windows DPAPI, macOS Keychain helper."""
import base64
import ctypes
from getpass import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

from .notify import _mailboxes, send_email


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
    helper = config.get("capture", {}).get("mac_helper")
    if not helper or not Path(helper).is_file():
        raise RuntimeError("Build the macOS helper before configuring Keychain credentials")
    request = dict(service="wechat-lecture-tracker", account=config["group"])
    if value is not None:
        request["value"] = json.dumps(value)
    result = subprocess.run([str(helper), action], input=json.dumps(request).encode(), capture_output=True, timeout=30)
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


def apply_credentials(config):
    email = config.get("email", {})
    fields = ("smtp_user_env", "smtp_password_env", "from_env", "to_env")
    if sys.platform == "win32":
        path = Path(config["data_dir"]) / "email.credentials"
        if path.is_file():
            values = json.loads(_dpapi(path.read_bytes(), decrypt=True))
            for field in fields:
                if field not in values or not isinstance(values[field], str):
                    raise RuntimeError("Encrypted email credential data is incomplete")
                os.environ[email[field]] = values[field]
    elif sys.platform == "darwin" and (Path(config["data_dir"]) / "email.keychain").is_file():
        values = _keychain(config, "credential-get")
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
        send_email("微信讲座监控配置测试", "这是一封本机服务配置测试邮件。收到后，服务可按配置发送讲座摘要和到场前提醒。", config["email"])
        save_credentials(config, values)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"configured": True, "test_email_sent": True, "secret_storage": "DPAPI" if sys.platform == "win32" else "macOS Keychain"}
