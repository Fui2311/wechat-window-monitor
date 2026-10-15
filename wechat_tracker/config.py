from pathlib import Path
import tomllib


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if not isinstance(config.get("group"), str) or not config["group"].strip():
        raise ValueError("config.group 必须是精确群名")
    if config.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
        raise ValueError("当前时间规则仅支持 Asia/Shanghai")
    minutes = config.setdefault("reminder_minutes", [1440, 60])
    if not isinstance(minutes, list) or any(type(n) is not int or n < 0 for n in minutes):
        raise ValueError("reminder_minutes 必须是非负整数分钟列表")
    if type(config.get("max_attempts", 5)) is not int or config.get("max_attempts", 5) < 1:
        raise ValueError("max_attempts 必须大于0")
    config["data_dir"] = str((path.parent / config.get("data_dir", "state")).resolve())
    config.setdefault("email", {})
    capture = config.setdefault("capture", {"source": "windows_uia"})
    if not isinstance(capture, dict):
        raise ValueError("capture必须是配置表")
    if type(capture.get("auto_expand_forward", False)) is not bool:
        raise ValueError("auto_expand_forward必须是布尔值")
    for key, default, low, high in (("forward_max_cards", 3, 1, 10),
                                   ("forward_max_pages", 5, 1, 20),
                                   ("forward_idle_seconds", 3, 1, 60),
                                   ("forward_partial_retry_seconds", 600, 60, 86400),
                                   ("forward_timeout_seconds", 25, 1, 60)):
        value = capture.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"capture.{key}必须在{low}到{high}之间")
    return config

