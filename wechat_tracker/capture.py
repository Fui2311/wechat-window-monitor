"""Fail-closed, read-only capture of one explicitly configured Weixin group.

Windows UIA reads only the independently verified group message list. JSONL is
an explicit alternative input; neither backend opens attachments or sends chat.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


class CaptureError(RuntimeError):
    """Capture could not prove the requested group's message source."""


def _settings(config: dict) -> tuple[str, dict]:
    group = config.get("group")
    if not isinstance(group, str) or not group.strip():
        raise CaptureError("A nonempty, exact top-level group name is required")
    options = config.get("capture", {})
    if not isinstance(options, dict):
        raise CaptureError("capture must be a configuration object")
    return group, options


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(group: str, kind: str, text: str, occurrence: int) -> str:
    payload = json.dumps([group, kind, text, occurrence], ensure_ascii=False, separators=(",", ":"))
    return "uia:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _classify(text: str, class_name: str) -> str:
    if class_name == "mmui::ChatTextItemView":
        return "text"
    first = text.splitlines()[0].strip()
    if first == "文件" or first.startswith("文件 "):
        return "file"
    if first.startswith(("[聊天记录]", "聊天记录", "合并转发", "转发的聊天记录", "群聊的聊天记录")) or re.match(r".+和.+的聊天记录(?:\s|$)", first):
        return "forward"
    if first in {"图片", "[图片]"}:
        return "image"
    if first in {"视频", "[视频]"}:
        return "video"
    if first.startswith(("[语音]", "语音")):
        return "audio"
    return "card"


def _run_uia(group: str, options: dict, *, mode: str) -> dict:
    if sys.platform != "win32":
        raise CaptureError("windows_uia requires an interactive Windows desktop")
    executable = options.get("powershell") or shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not executable:
        raise CaptureError("PowerShell is unavailable")
    resource = "expand-weixin-forward.ps1" if mode == "expand" else "probe-weixin.ps1"
    script = Path(__file__).resolve().parent / "resources" / resource
    command = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Group", group, "-Mode", mode]
    process_name = options.get("process_name", "Weixin")
    if not isinstance(process_name, str) or not process_name:
        raise CaptureError("capture.process_name must be a nonempty string")
    command.extend(["-ProcessName", process_name])
    if mode in {"forward", "expand"}:
        command.extend(["-ForwardWindowTitle", str(options.get("forward_window_title", ""))])
        if mode == "forward" and options.get("forward_window_handle") is not None:
            command.extend(["-TargetWindowHandle", str(int(options["forward_window_handle"]))])
    if mode == "expand":
        command.extend(["-GroupWindowHandle", str(options["group_window_handle"]),
                        "-TargetRuntimeId", ",".join(map(str, options["expand_runtime_id"])),
                        "-ExpectedTextSha256", options["expand_expected_sha256"],
                        "-MaxPages", str(options.get("forward_max_pages", 5)),
                        "-IdleSeconds", str(options.get("forward_idle_seconds", 3))])
    if options.get("process_id") is not None:
        command.extend(["-TargetProcessId", str(int(options["process_id"]))])
    max_items = int(options.get("max_items", 200))
    if not 1 <= max_items <= 1000:
        raise CaptureError("capture.max_items must be between 1 and 1000")
    command.extend(["-MaxItems", str(max_items)])
    timeout = float(options.get("forward_timeout_seconds", 25) if mode == "expand" else options.get("timeout_seconds", 8))
    if not 0 < timeout <= 60:
        raise CaptureError("capture.timeout_seconds must be greater than 0 and at most 60")
    try:
        completed = subprocess.run(command, capture_output=True, encoding="utf-8-sig", errors="strict", timeout=timeout, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"UIA probe timed out after {timeout:g} seconds") from exc
    except (OSError, UnicodeError) as exc:
        raise CaptureError(f"UIA subprocess failed: {exc}") from exc
    try:
        result = json.loads(completed.stdout.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        # Do not include arbitrary stdout/stderr: they may contain chat content.
        raise CaptureError("UIA probe did not return a single valid JSON object") from exc
    if not isinstance(result, dict):
        raise CaptureError("UIA probe returned an invalid result shape")
    if completed.returncode != 0 and result.get("ok"):
        raise CaptureError("UIA probe reported success with a failing exit code")
    if result.get("group") != group:
        raise CaptureError("UIA probe returned a different group")
    return result


def _read_jsonl(group: str, options: dict) -> list[dict]:
    value = options.get("jsonl_path")
    if not isinstance(value, str) or not value:
        raise CaptureError("capture.jsonl_path is required for source=jsonl")
    path = Path(value).expanduser()
    if not path.is_file():
        raise CaptureError(f"JSONL input does not exist: {path}")
    observed_at = _now()
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise CaptureError(f"JSONL line {number} must be an object")
                if item.get("group") != group:
                    raise CaptureError(f"JSONL line {number} does not match the exact configured group")
                if not isinstance(item.get("text"), str) or not item["text"].strip():
                    raise CaptureError(f"JSONL line {number} requires nonempty text")
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise CaptureError(f"JSONL line {number} requires an explicit stable id")
                for field in ("sender", "sent_at"):
                    if item.get(field) is not None and not isinstance(item[field], str):
                        raise CaptureError(f"JSONL line {number}: {field} must be a string or null")
                kind = item.get("kind", "text")
                if not isinstance(kind, str) or not kind:
                    raise CaptureError(f"JSONL line {number}: kind must be a nonempty string")
                _validate_export_nesting(item, group)
                row = {"id": item["id"], "group": group, "sender": item.get("sender"), "sent_at": item.get("sent_at"), "text": item["text"], "kind": "forward" if kind == "forwarded" else kind, "observed_at": observed_at, "source": "jsonl", "source_meta": {"input_line": number}}
                for field in ("children", "attachments"):
                    if field in item:
                        row[field] = item[field]
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"Cannot read JSONL input: {exc}") from exc
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise CaptureError("JSONL input contains duplicate ids")
    return rows


def _validate_export_nesting(item: dict, group: str, depth: int = 0, budget: list | None = None) -> None:
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 6 or budget[0] > 200:
        raise CaptureError("JSONL nested history exceeds 6 levels or 200 messages")
    if not isinstance(item, dict) or item.get("group", group) != group:
        raise CaptureError("JSONL nested history does not match the exact configured group")
    for field in ("children", "attachments"):
        if not isinstance(item.get(field, []), list):
            raise CaptureError(f"JSONL {field} must be a list")
    if any(not isinstance(attachment, dict) for attachment in item.get("attachments", [])):
        raise CaptureError("JSONL attachments must contain objects")
    for child in item.get("children", []):
        _validate_export_nesting(child, group, depth + 1, budget)


def normalize_forward_snapshot(snapshot: dict, config: dict, parent_id: str) -> dict:
    """Pure parser for a user-bound, already-open merged-history snapshot.

    This function performs no UI calls. It accepts snapshots produced by the
    PowerShell reader or manually transcribed from a verified computer-use tree.
    The generic history title cannot independently prove its original group.
    """
    group, options = _settings(config)
    if not isinstance(parent_id, str) or not parent_id.strip():
        raise CaptureError("An explicit local parent_id is required")
    if options.get("forward_parent_id") != parent_id:
        raise CaptureError("capture.forward_parent_id must explicitly bind this parent_id")
    expected_title = options.get("forward_window_title")
    if not isinstance(expected_title, str) or not (expected_title == "群聊的聊天记录" or re.fullmatch(r".+和.+的聊天记录", expected_title)):
        raise CaptureError("An exact supported forward_window_title is required")
    if snapshot.get("window_title") != expected_title or snapshot.get("process_name") != options.get("process_name", "Weixin"):
        raise CaptureError("The merged-history window title or process identity does not match")
    for field, setting in (("process_id", "process_id"), ("window_handle", "forward_window_handle")):
        actual = snapshot.get(field, snapshot.get("window_id") if field == "window_handle" else None)
        if field == "process_id" and actual is None and snapshot.get("source") == "computer_use_accessibility":
            continue  # The documented computer-use API exposes process name, not PID.
        if options.get(setting) is not None and str(actual) != str(options[setting]):
            raise CaptureError(f"The merged-history {field} does not match the explicit binding")
    if snapshot.get("list_count") != 1 or not isinstance(snapshot.get("items"), list):
        raise CaptureError("Merged history requires exactly one verified List and its direct items")
    observed_at = snapshot.get("observed_at")
    if not isinstance(observed_at, str):
        raise CaptureError("Merged history requires an actual observed_at timestamp")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise CaptureError("Merged-history observed_at must be a timestamp with timezone") from exc
    children, incomplete_reasons = [], ["history_completeness_not_proven"]
    counter: Counter[tuple[str, str]] = Counter()
    if len(snapshot["items"]) > int(options.get("max_items", 200)):
        raise CaptureError("Merged history exceeds the configured safe snapshot size")
    for index, item in enumerate(snapshot["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise CaptureError("Merged history contains an invalid item")
        raw_text = item["text"].replace("\\n", "\n").strip()
        if not raw_text:
            incomplete_reasons.append(f"empty_or_virtualized_item:{index}")
            continue
        if item.get("automation_id") != "qt_scrollarea_viewport.chat_bubble_item_view":
            raise CaptureError("An unverified merged-history row AutomationId was returned")
        if item.get("is_offscreen"):
            incomplete_reasons.append(f"offscreen_item:{index}")
            continue
        text, sent_at = raw_text, None
        # Only a complete date+time at the end is metadata. Do not delete dates
        # appearing inside the announcement or infer a year from observation.
        stamp = re.search(r"\s+(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})\s*$", raw_text)
        if stamp:
            try:
                parsed = datetime.strptime(stamp.group(1), "%Y年%m月%d日 %H:%M")
                sent_at = parsed.replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
                text = raw_text[:stamp.start()].rstrip()
            except ValueError:
                incomplete_reasons.append(f"invalid_timestamp_suffix:{index}")
        kind = item.get("kind") or _classify(text, item.get("class_name", "mmui::ChatBubbleItemView"))
        if kind == "card" and not item.get("class_name"):
            kind = "text"  # AX snapshots expose each plain-text body directly.
        kind = "forward" if kind == "forwarded" else kind
        if kind not in {"text", "file", "image", "video", "audio", "forward", "card"}:
            raise CaptureError("Merged history contains an unsupported message kind")
        ordinal = counter[(kind, text)]
        counter[(kind, text)] += 1
        child_id = parent_id + ":" + _fingerprint(group, kind, text, ordinal).removeprefix("uia:")
        children.append({"id": child_id, "group": group, "sender": item.get("sender"), "sent_at": sent_at, "text": text, "kind": kind, "observed_at": observed_at, "source": "windows_uia_forward", "source_meta": {"raw_text": raw_text, "parent_id": parent_id, "source_group_verified": False, "identity_binding": "explicit_local_parent_id", "pending_expansion": kind != "text", "timestamp_basis": "trailing_accessible_full_date_time" if sent_at else None}})
    return {"id": parent_id, "group": group, "sender": None, "sent_at": None, "text": expected_title, "kind": "forward", "observed_at": observed_at, "source": "windows_uia_forward", "children": children, "attachments": [], "source_group_verified": False, "incomplete": bool(incomplete_reasons), "source_meta": {"source_group_verified": False, "identity_binding": "explicit_local_parent_id", "window_title": expected_title, "window_handle": snapshot.get("window_handle", snapshot.get("window_id")), "process_id_verified": snapshot.get("process_id") is not None, "snapshot_source": snapshot.get("source", "windows_uia"), "coverage": snapshot.get("coverage", "loaded snapshot only; no automatic expansion or scrolling"), "incomplete_reasons": incomplete_reasons, "attachments_downloaded": False}}


def read_forward(config: dict, parent_id: str, *, snapshot: dict | None = None) -> dict:
    """Read an already-open, explicitly bound merged window; never opens it."""
    group, options = _settings(config)
    # Validate the binding before doing any Windows I/O.
    if not isinstance(parent_id, str) or not parent_id or options.get("forward_parent_id") != parent_id:
        raise CaptureError("capture.forward_parent_id must explicitly bind this parent_id")
    if not options.get("forward_window_title"):
        raise CaptureError("capture.forward_window_title is required")
    if snapshot is None:
        result = _run_uia(group, options, mode="forward")
        if result.get("ok") is not True:
            raise CaptureError(f"{result.get('status', 'uia_error')}: {result.get('error', 'merged history is unavailable')}")
        snapshot = result
    return normalize_forward_snapshot(snapshot, config, parent_id)


def expand_forward(config: dict, message: dict) -> dict:
    """Open one freshly bound UIA card. The native driver verifies each action.

    Default capture remains read-only; only an explicit watcher option calls this.
    Generic already-open detail windows are never treated as a successful open.
    """
    group, options = _settings(config)
    meta = message.get("source_meta", {})
    if (message.get("group") != group or message.get("kind") != "forward"
            or message.get("source") != "windows_uia" or not message.get("id")
            or not isinstance(message.get("text"), str)):
        raise CaptureError("Automatic expansion requires a captured card from the exact group")
    runtime = meta.get("runtime_id")
    if (not isinstance(runtime, list) or not runtime or
            any(type(n) is not int for n in runtime) or
            type(meta.get("window_handle")) is not int or meta["window_handle"] <= 0 or
            type(meta.get("process_id")) is not int or meta["process_id"] <= 0):
        raise CaptureError("Automatic expansion requires a current runtime id, window and process binding")
    if not meta.get("fully_visible"):
        raise CaptureError("forward_card_clipped: show the entire forward card before opening it")
    if _classify(message["text"], meta.get("class_name", "")) != "forward":
        raise CaptureError("The bound message is not a recognized merged-history card")
    title = options.get("forward_window_title", "群聊的聊天记录")
    if not isinstance(title, str) or not (title == "群聊的聊天记录" or re.fullmatch(r".+和.+的聊天记录", title)):
        raise CaptureError("An exact supported forward_window_title is required")
    text_hash = hashlib.sha256(message["text"].encode("utf-8")).hexdigest()
    native_options = dict(options, group_window_handle=meta["window_handle"],
                          process_id=meta["process_id"], expand_runtime_id=runtime,
                          expand_expected_sha256=text_hash, forward_window_title=title)
    result = _run_uia(group, native_options, mode="expand")
    if result.get("ok") is not True or result.get("status") != "ready":
        raise CaptureError(f"{result.get('status', 'uia_error')}: {result.get('error', 'forward expansion failed')}")
    binding = result.get("binding", {})
    if (binding.get("group_window_handle") != meta["window_handle"] or
            binding.get("parent_runtime_id") != runtime or
            binding.get("parent_text_sha256") != text_hash or
            binding.get("new_window_observed") is not True or
            binding.get("restored") is not True or
            binding.get("detail_window_handle") != result.get("window_handle") or
            result.get("process_id") != meta["process_id"]):
        raise CaptureError("forward_binding_failed: native driver did not prove open/read/return linkage")
    native_options.update(forward_parent_id=message["id"], forward_window_handle=result["window_handle"])
    parent = normalize_forward_snapshot(result, dict(config, capture=native_options), message["id"])
    parent.update(text=message["text"], sender=message.get("sender"), sent_at=message.get("sent_at"),
                  source_group_verified=True)
    parent["source_meta"].update(source_group_verified=True, identity_binding="verified_parent_open",
                                  binding=binding, history_complete=result.get("history_complete") is True)
    reasons = parent["source_meta"]["incomplete_reasons"]
    if result.get("history_complete") is True:
        reasons.remove("history_completeness_not_proven")
    reasons.extend(result.get("incomplete_reasons", []))
    parent["incomplete"] = bool(reasons)
    for child in parent["children"]:
        child["source_meta"].update(source_group_verified=True, identity_binding="verified_parent_open")
    return parent


def probe(config: dict) -> dict:
    """Return structured readiness diagnostics without leaking message bodies."""
    group: str | None = None
    try:
        group, options = _settings(config)
        source = options.get("source", "windows_uia")
        if source == "jsonl":
            rows = _read_jsonl(group, options)
            return {"ok": True, "status": "ready", "source": source, "group": group, "message_count": len(rows), "observed_at": _now()}
        if source != "windows_uia":
            raise CaptureError(f"Unsupported capture source: {source}")
        return _run_uia(group, options, mode="probe")
    except (CaptureError, ValueError, TypeError) as exc:
        return {"ok": False, "status": "capture_error", "group": group, "error": str(exc), "observed_at": _now()}


def capture_once(config: dict) -> list[dict]:
    """Read a visible snapshot; raise instead of guessing another UI selector."""
    group, options = _settings(config)
    source = options.get("source", "windows_uia")
    if source == "jsonl":
        return _read_jsonl(group, options)
    if source != "windows_uia":
        raise CaptureError(f"Unsupported capture source: {source}")
    result = _run_uia(group, options, mode="capture")
    if result.get("ok") is not True or result.get("status") != "ready":
        raise CaptureError(f"{result.get('status', 'uia_error')}: {result.get('error', 'UIA source not ready')}")
    if not isinstance(result.get("observed_at"), str) or not isinstance(result.get("messages"), list):
        raise CaptureError("UIA probe returned an incomplete snapshot")
    occurrence: Counter[tuple[str, str]] = Counter()
    rows = []
    for item in result["messages"]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise CaptureError("UIA probe returned an unreadable message row")
        class_name = item.get("class_name")
        if class_name not in {"mmui::ChatTextItemView", "mmui::ChatBubbleItemView"}:
            raise CaptureError("UIA probe returned an unverified message row class")
        text = item["text"]
        kind = _classify(text, class_name)
        key = (kind, text)
        ordinal = occurrence[key]
        occurrence[key] += 1
        rows.append({"id": _fingerprint(group, kind, text, ordinal), "group": group, "sender": None, "sent_at": None, "text": text, "kind": kind, "observed_at": result["observed_at"], "source": "windows_uia", "source_meta": {"class_name": class_name, "runtime_id": item.get("runtime_id"), "window_handle": result.get("window_handle"), "process_id": result.get("process_id"), "fully_visible": item.get("fully_visible", False), "pending_expansion": kind != "text", "identity": "content fingerprint plus visible duplicate ordinal; not a Weixin message id"}})
    return rows
