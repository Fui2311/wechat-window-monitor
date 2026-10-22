"""Bounded JSON Chat Completions transport; local settings never expose secrets."""

import json
import math
import os
from pathlib import Path
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from wechat_tracker.config import atomic_write_config

DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "model": "",
    "timeout": 20,
    "api_key_env": "WECHAT_MONITOR_API_KEY",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ModelSettings:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "model.json"
        self.key_path = self.root / ".model-key"

    def read(self):
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        return {key: saved.get(key, default) for key, default in DEFAULTS.items()}

    def public(self):
        cfg = self.read()
        return dict(
            cfg,
            key_configured=self.key_path.is_file()
            or bool(os.environ.get(cfg["api_key_env"])),
        )

    def save(self, body):
        if not isinstance(body, dict) or set(body) - set(DEFAULTS) - {
            "api_key",
            "clear_key",
        }:
            raise ValueError("未知模型设置")
        cfg = self.read()
        cfg.update({k: body[k] for k in DEFAULTS if k in body})
        for key in ("base_url", "model", "api_key_env"):
            if (
                not isinstance(cfg[key], str)
                or len(cfg[key]) > 1000
                or any(ord(c) < 32 for c in cfg[key])
            ):
                raise ValueError("模型设置必须为单行文本")
        url = urlsplit(cfg["base_url"])
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("接口必须为不含凭据或参数的HTTP(S)基础地址")
        try:
            url.port
        except ValueError:
            raise ValueError("接口端口无效") from None
        if url.scheme == "http" and url.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("远程模型接口必须使用HTTPS")
        if (
            type(cfg["timeout"]) not in (int, float)
            or not math.isfinite(cfg["timeout"])
            or not 1 <= cfg["timeout"] <= 60
        ):
            raise ValueError("模型超时应为1至60秒")
        import re

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cfg["api_key_env"]):
            raise ValueError("密钥环境变量名无效")
        key = body.get("api_key", "")
        if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 for c in key):
            raise ValueError("密钥须为单行文本")
        if type(body.get("clear_key", False)) is not bool or (
            key and body.get("clear_key")
        ):
            raise ValueError("不可同时保存与清除密钥")
        atomic_write_config(self.path, json.dumps(cfg, ensure_ascii=False))
        if key.strip():
            atomic_write_config(self.key_path, key.strip())
        elif body.get("clear_key"):
            self.key_path.unlink(missing_ok=True)
        return self.public()


class ChatClient:
    def __init__(self, settings):
        self.settings = settings

    def complete(self, system, data):
        cfg = self.settings.read()
        if not cfg["model"].strip():
            raise ValueError("请先在模型设置中填写接口和模型名称")
        key = (
            self.settings.key_path.read_text().strip()
            if self.settings.key_path.is_file()
            else os.environ.get(cfg["api_key_env"], "")
        )
        endpoint = cfg["base_url"].rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        body = json.dumps(
            {
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(
            endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(
                req, timeout=cfg["timeout"]
            ) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError("模型响应超过256KB")
            content = json.loads(raw)["choices"][0]["message"]["content"]
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as exc:
            raise ValueError(f"模型接口 HTTP {exc.code}，请核对设置后重试") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("模型连接失败或超时，原始消息已保留") from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ValueError("模型未返回约定JSON结构") from None
