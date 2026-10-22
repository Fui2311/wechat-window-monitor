import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib


def find_default_config_path() -> Path:
    """Finds the default config file with prioritized discovery."""
    # 1. macOS Application Support live config
    app_support = Path.home() / "Library" / "Application Support" / "WeChatLectureTracker" / "config.toml"
    if app_support.is_file():
        return app_support.resolve()
    # 2. Local config.local.toml in cwd
    cwd_local = Path.cwd() / "config.local.toml"
    if cwd_local.is_file():
        return cwd_local.resolve()
    # 3. Local config.toml in cwd
    cwd_config = Path.cwd() / "config.toml"
    if cwd_config.is_file():
        return cwd_config.resolve()
    # 4. Local config.example.toml in cwd
    cwd_example = Path.cwd() / "config.example.toml"
    if cwd_example.is_file():
        return cwd_example.resolve()
    # 5. Repo fallback
    repo_example = Path(__file__).resolve().parent.parent / "config.example.toml"
    if repo_example.is_file():
        return repo_example.resolve()
    return cwd_example.resolve()


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Resolves explicit path if given, else finds default."""
    if path is not None and str(path).strip():
        p = Path(path).expanduser()
        return p if p.is_absolute() else p.resolve()
    return find_default_config_path()


def _format_toml_val(val: object) -> str:
    if isinstance(val, str):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        return json.dumps(val, ensure_ascii=False)
    raise TypeError(f"Unsupported TOML value type: {type(val)}")


def atomic_write_config(path: Path | str, content: str) -> None:
    path = Path(path).resolve()
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_toml_root_keys(config_path: Path | str, updates: dict[str, object]) -> tuple[str, str]:
    """Updates root-level keys in a TOML file while preserving all other keys,
    comments, blank lines, section headers, nested tables and indentation.
    Performs atomic write with validation and returns (old_content, new_content).
    """
    path = Path(config_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open(encoding="utf-8", newline="") as handle:
        old_content = handle.read()
    lines = old_content.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in old_content else "\n"

    root_lines: list[str] = []
    rest_lines: list[str] = []
    reached_section = False
    in_multiline: str | None = None
    updated_keys = set()

    for line in lines:
        if reached_section:
            rest_lines.append(line)
            continue

        if in_multiline:
            if in_multiline in line:
                in_multiline = None
            root_lines.append(line)
            continue

        # Check if line is a table header [section] or [[array]]
        table_match = re.match(r"^\s*\[\[?[^\]]+\]\]?\s*(?:#.*)?$", line)
        if table_match:
            reached_section = True
            rest_lines.append(line)
            continue

        # Check if this line starts a multiline string
        for delim in ('"""', "'''"):
            if line.count(delim) % 2 == 1:
                in_multiline = delim
                break

        # Check if line matches a root update key
        matched_key = None
        for k in updates:
            pattern = rf"^(\s*)([\"']?{re.escape(k)}[\"']?)(\s*=)(.*)$"
            m = re.match(pattern, line)
            if m:
                matched_key = k
                indent = m.group(1)
                key_str = m.group(2)
                rest = m.group(4)
                val_repr = _format_toml_val(updates[k])
                comment_match = re.search(r"(\s*#.*)$", rest)
                comment_part = comment_match.group(1) if comment_match else newline
                if not comment_part.endswith(newline):
                    comment_part += newline
                root_lines.append(f"{indent}{key_str} = {val_repr}{comment_part}")
                updated_keys.add(k)
                break

        if matched_key is None:
            root_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            val_repr = _format_toml_val(v)
            root_lines.append(f"{k} = {val_repr}{newline}")

    new_content = "".join(root_lines + rest_lines)

    # Validate that generated content is valid TOML with expected updates
    parsed = tomllib.loads(new_content)
    expected = tomllib.loads(old_content)
    expected.update(updates)
    if parsed != expected:
        raise ValueError("群切换仅允许修改根级设置；配置内容未保存")
    atomic_write_config(path, new_content)

    return old_content, new_content


def resolve_group_data_dir(
    base_state_dir: Path | str,
    new_group: str,
    current_group: str | None = None,
    current_data_dir: Path | str | None = None
) -> Path:
    """Finds existing data_dir for new_group or creates a stable deterministic one."""
    target_group = new_group.strip()
    if not target_group:
        raise ValueError("Group name cannot be empty")

    base = Path(base_state_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)

    if current_group and current_data_dir and target_group == current_group.strip():
        cur_path = Path(current_data_dir).resolve()
        cur_path.mkdir(parents=True, exist_ok=True)
        return cur_path

    # Search existing subdirectories in base
    for sub in base.iterdir():
        if not sub.is_dir():
            continue
        sub_name = sub.name.lower()
        if "backup" in sub_name or "tmp" in sub_name or sub_name.startswith("."):
            continue

        metadata = sub / "group.json"
        if metadata.is_file():
            try:
                if json.loads(metadata.read_text(encoding="utf-8")).get("group") == target_group:
                    return sub
            except (OSError, ValueError):
                pass

        svc_json = sub / "service.json"
        if svc_json.is_file():
            try:
                data = json.loads(svc_json.read_text(encoding="utf-8"))
                if data.get("group") == target_group:
                    return sub
            except Exception:
                pass

        db_file = sub / "tracker.sqlite3"
        if db_file.is_file():
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=2)
                try:
                    row = conn.execute("SELECT group_name FROM capture_state LIMIT 1").fetchone()
                    if row and row[0] == target_group:
                        return sub
                    row = conn.execute("SELECT group_name FROM events LIMIT 1").fetchone()
                    if row and row[0] == target_group:
                        return sub
                finally:
                    conn.close()
            except Exception:
                pass

    # Deterministic stable name
    hash8 = hashlib.sha256(target_group.encode("utf-8")).hexdigest()[:8]
    clean_name = re.sub(r'[^\w\u4e00-\u9fa5\-]+', '_', target_group).strip('_')
    dir_name = f"group-{clean_name[:20]}-{hash8}" if clean_name else f"group-{hash8}"
    dest = base / dir_name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def load_config(path: str | Path) -> dict:
    path = Path(path).resolve()
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if not isinstance(config.get("group"), str) or not config["group"].strip():
        raise ValueError("config.group 必须是精确群名")
    if config.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
        raise ValueError("当前时间规则仅支持 Asia/Shanghai")
    minutes = config.setdefault("reminder_minutes", [])
    if not isinstance(minutes, list) or any(type(n) is not int or n < 0 for n in minutes):
        raise ValueError("reminder_minutes 必须是非负整数分钟列表")
    if type(config.get("max_attempts", 5)) is not int or config.get("max_attempts", 5) < 1:
        raise ValueError("max_attempts 必须大于0")
    config["data_dir"] = str((path.parent / Path(os.path.expandvars(config.get("data_dir", "state"))).expanduser()).resolve())
    config["config_path"] = str(path)
    config.setdefault("email", {})
    capture = config.setdefault("capture", {"source": "windows_uia"})
    if not isinstance(capture, dict):
        raise ValueError("capture必须是配置表")
    if type(capture.get("auto_expand_forward", False)) is not bool:
        raise ValueError("auto_expand_forward必须是布尔值")
    for key in ("follow_tail", "recover_window", "forward_wait_for_body"):
        if type(capture.get(key, key == "follow_tail")) is not bool:
            raise ValueError(f"capture.{key}必须是布尔值")
    if capture.get("mac_message_identity", "content") not in {"content", "ax_row"}:
        raise ValueError("capture.mac_message_identity必须为content或ax_row")
    if capture.get("mac_forward_interaction", "ax_press") not in ("ax_press", "activate_click"):
        raise ValueError("capture.mac_forward_interaction必须为ax_press或activate_click")
    if capture.get("mac_forward_card_side", "right") not in ("left", "right"):
        raise ValueError("capture.mac_forward_card_side必须为left或right")
    for key, default, low, high in (("forward_max_cards", 3, 1, 10),
                                   ("forward_max_pages", 5, 1, 20),
                                   ("forward_idle_seconds", 3, 1, 60),
                                   ("forward_notice_seconds", 5, 5, 15),
                                   ("forward_postpone_seconds", 300, 60, 3600),
                                   ("forward_partial_retry_seconds", 600, 60, 86400),
                                   ("forward_timeout_seconds", 25, 1, 60)):
        value = capture.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"capture.{key}必须在{low}到{high}之间")
    if (capture.get("mac_forward_interaction") == "activate_click" and
            capture.get("forward_timeout_seconds", 25) < capture.get("forward_notice_seconds", 5) + 10):
        raise ValueError("自动切换微信的超时须至少覆盖预告时间加10秒")
    service = config.setdefault("service", {})
    if not isinstance(service, dict):
        raise ValueError("service必须是配置表")
    for key, default, low, high in (("resync_seconds", 15, 2, 60),
                                   ("dispatch_seconds", 5, 1, 60),
                                   ("worker_stale_seconds", 90, 30, 300)):
        value = service.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"service.{key}必须在{low}到{high}之间")
    for key in ("include_existing", "prevent_sleep", "backup_enabled"):
        if type(service.get(key, True)) is not bool:
            raise ValueError(f"service.{key}必须为布尔值")
    from .extraction.settings import validate_settings
    validate_settings(config)
    return config
