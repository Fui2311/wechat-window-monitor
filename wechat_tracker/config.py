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
    config.setdefault("capture", {"source": "windows_uia"})
    return config

