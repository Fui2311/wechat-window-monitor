"""Window settings are explicit and separate from the legacy installed service."""

from .capture import default_source
from .recognition.schema import text

CAPTURE_FIELDS = {
    "source",
    "mac_helper",
    "jsonl_path",
    "mac_header_identifier",
    "mac_list_identifier",
    "process_name",
}


def window_config(body):
    if not isinstance(body, dict) or set(body) - {
        "name",
        "target",
        "mode",
        "interval",
        "include_existing",
        "capture",
        "revision",
    }:
        raise ValueError("未知窗口配置项")
    name = text(body.get("name"), "精确窗口名称", 200)
    name = body[
        "name"
    ]  # Preserve exact title whitespace; never bind a silently trimmed name.
    if any(ord(c) < 32 for c in name):
        raise ValueError("窗口名称不能含控制字符")
    target = text(body.get("target"), "识别目标")
    mode = body.get("mode", "local")
    if mode not in {"local", "llm"}:
        raise ValueError("识别模式无效")
    interval = body.get("interval", 10)
    if type(interval) is not int or not 1 <= interval <= 300:
        raise ValueError("采样间隔须为1至300秒整数")
    include = body.get("include_existing", False)
    if type(include) is not bool:
        raise ValueError("首次分析开关必须为布尔值")
    capture = body.get("capture", {"source": default_source()})
    if (
        not isinstance(capture, dict)
        or set(capture) - CAPTURE_FIELDS
        or capture.get("source") not in {"macos_ax", "windows_uia", "jsonl"}
    ):
        raise ValueError("采集来源或配置项无效")
    for key, value in capture.items():
        text(value, key, 2000, required=key == "source")
    capture = {key: value.strip() for key, value in capture.items() if value.strip()}
    if capture["source"] == "jsonl" and not capture.get("jsonl_path"):
        raise ValueError("JSONL离线输入需要文件路径")
    return dict(
        name=name,
        target=target,
        mode=mode,
        interval=interval,
        include_existing=include,
        capture=capture,
    )
