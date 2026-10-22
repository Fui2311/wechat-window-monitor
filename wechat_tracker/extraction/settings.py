"""Validated processing settings and local atomic credential persistence."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from ..config import load_config, update_toml_root_keys

DEFAULTS = dict(processing_mode="rules", llm_base_url="https://api.openai.com/v1",
                llm_model="", llm_api_key_env="WECHAT_LLM_API_KEY", llm_timeout_seconds=15,
                llm_disable_thinking=False)
FIELDS = set(DEFAULTS)


class ProcessingError(ValueError):
    pass


def settings(config):
    return {key: config.get(key, value) for key, value in DEFAULTS.items()}


def validate_settings(config):
    cfg = settings(config)
    if cfg["processing_mode"] not in {"rules", "llm"}:
        raise ValueError("处理方式必须为 rules 或 llm")
    if type(cfg["llm_disable_thinking"]) is not bool:
        raise ValueError("关闭 thinking 必须为布尔值")
    for key in ("llm_base_url", "llm_model", "llm_api_key_env"):
        if not isinstance(cfg[key], str) or len(cfg[key]) > 1000 or any(ord(c) < 32 for c in cfg[key]):
            raise ValueError(f"{key} 必须为单行文本")
    url = urlsplit(cfg["llm_base_url"])
    if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("接口地址必须为不含密钥、参数或片段的 HTTP(S) 基础地址")
    try:
        url.port
    except ValueError:
        raise ValueError("接口地址端口无效") from None
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程接口必须使用 HTTPS；本地接口可使用 HTTP")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cfg["llm_api_key_env"]):
        raise ValueError("密钥环境变量名称无效")
    timeout = cfg["llm_timeout_seconds"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise ValueError("LLM 超时必须为 1–60 秒")
    if cfg["processing_mode"] == "llm" and not cfg["llm_model"].strip():
        raise ValueError("请填写模型名称后启用 LLM")
    return cfg


def key_path(config):
    path = config.get("config_path")
    return Path(str(path) + ".llm-key") if path else None


def api_key(config):
    path = key_path(config)
    if path and path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(settings(config)["llm_api_key_env"], "").strip()


def effective_processing_config(config):
    """Load only processing settings from an explicitly linked config, keeping group isolation."""
    fresh = load_config(config["config_path"]) if config.get("config_path") else config
    linked = fresh.get("processing_config_path")
    if linked:
        if not isinstance(linked, str):
            raise ValueError("processing_config_path 必须是配置文件路径")
        path = Path(linked).expanduser()
        if not path.is_absolute():
            path = Path(fresh["config_path"]).parent / path
        source = load_config(path)
        if source.get("processing_config_path"):
            raise ValueError("处理配置不支持多层引用")
    else:
        source = fresh
    active = dict(config, **settings(source))
    if source.get("config_path"):
        active["config_path"] = source["config_path"]
    active["processing_settings_shared"] = bool(linked)
    return active


def public_settings(config):
    active = effective_processing_config(config)
    result = settings(active)
    path = key_path(active)
    result["settings_shared"] = active["processing_settings_shared"]
    result["settings_source"] = str(active.get("config_path", ""))
    result["api_key_configured"] = bool((path and path.is_file()) or os.environ.get(result["llm_api_key_env"]))
    return result


def _write_key(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".llm-key-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_settings(config, body):
    if set(body) - FIELDS - {"api_key", "clear_api_key", "text"}:
        raise ValueError("包含未知 LLM 配置字段")
    cfg = dict(config)
    cfg.update({k: body[k] for k in FIELDS if k in body})
    validate_settings(cfg)
    key = body.get("api_key", "")
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 32 for c in key):
        raise ValueError("API Key 必须为单行文本")
    if type(body.get("clear_api_key", False)) is not bool:
        raise ValueError("clear_api_key 必须为布尔值")
    if key.strip() and body.get("clear_api_key"):
        raise ValueError("不能同时更新和清除密钥")
    return cfg


def save_settings(config, body):
    active = effective_processing_config(config)
    if active["processing_settings_shared"]:
        raise ValueError(f"处理设置由共享配置管理，请在该配置的控制台修改：{active['config_path']}")
    cfg = prepare_settings(config, body)
    path = key_path(config)
    old_key = path.read_text(encoding="utf-8") if path.is_file() else None
    changed_key = bool(body.get("api_key", "").strip() or body.get("clear_api_key"))
    try:
        if body.get("api_key", "").strip():
            _write_key(path, body["api_key"].strip())
        elif body.get("clear_api_key"):
            path.unlink(missing_ok=True)
        # A key is optional for local compatible services.
        update_toml_root_keys(config["config_path"], settings(cfg))
    except Exception:
        if changed_key:
            if old_key is not None:
                _write_key(path, old_key)
            else:
                path.unlink(missing_ok=True)
        raise
    return load_config(config["config_path"])
