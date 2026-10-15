"""macOS Accessibility capture contracts; no Windows runtime or selectors."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from .capture import CaptureError, _classify, _fingerprint


def _helper(config, action, request=None):
    if sys.platform != "darwin":
        raise CaptureError("macos_ax requires a logged-in macOS desktop")
    options = config.get("capture", {})
    executable = options.get("mac_helper")
    if not executable or not Path(executable).is_file():
        raise CaptureError("Build the macOS Accessibility helper with mac/install.command")
    command = [str(executable), action, "--group", config["group"],
               "--bundle-id", options.get("bundle_id", "com.tencent.xinWeChat"),
               "--header-id", options.get("mac_header_identifier", ""),
               "--list-id", options.get("mac_list_identifier", "")]
    try:
        completed = subprocess.run(command, input=json.dumps(request).encode() if request is not None else None,
                                   capture_output=True, timeout=options.get("forward_timeout_seconds", 25))
        result = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise CaptureError("The macOS helper failed or exceeded its timeout") from None
    if not isinstance(result, dict) or result.get("group") != config["group"]:
        raise CaptureError("The macOS helper did not prove the exact configured group")
    return result


def normalize_snapshot(snapshot, config):
    group, options = config["group"], config.get("capture", {})
    if snapshot.get("ok") is not True:
        raise CaptureError(snapshot.get("status", "macos_capture_failed"))
    if (snapshot.get("group") != group or snapshot.get("source") != "macos_ax" or
            snapshot.get("bundle_id") != options.get("bundle_id", "com.tencent.xinWeChat") or
            snapshot.get("list_count") != 1 or snapshot.get("list_role") not in {"AXList", "AXTable"} or
            type(snapshot.get("process_id")) is not int or snapshot["process_id"] <= 0):
        raise CaptureError("Unverified macOS process, group or message list")
    binding = snapshot.get("group_binding", {})
    exact_window = snapshot.get("window_title") == group and binding.get("method") == "exact_window_title"
    exact_header = (bool(options.get("mac_header_identifier")) and
                    binding.get("header_identifier") == options["mac_header_identifier"] and
                    binding.get("header_value") == group and binding.get("method") == "explicit_header_identifier")
    if not (exact_window or exact_header):
        raise CaptureError("The macOS window does not independently prove the target group")
    observed = snapshot.get("observed_at")
    try:
        if datetime.fromisoformat(observed.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError()
    except (AttributeError, ValueError):
        raise CaptureError("macOS observation timestamp must include its timezone") from None
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) > min(199, options.get("max_items", 200)):
        raise CaptureError("macOS snapshot shape or size exceeds the capture bound")
    counters, messages = Counter(), []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            raise CaptureError("macOS returned an unreadable message row")
        text = item["text"].strip()
        kind = _classify(text, "AXRow")
        kind = "text" if kind == "card" else kind
        ordinal = counters[kind, text]
        counters[kind, text] += 1
        messages.append(dict(id="macax:" + _fingerprint(group, kind, text, ordinal)[4:], group=group,
                             sender=None, sent_at=None, observed_at=observed, text=text, kind=kind,
                             source="macos_ax", source_meta=dict(process_id=snapshot["process_id"],
                                 window_title=snapshot["window_title"], ax_row_index=item.get("row_index", index),
                                 fully_visible=item.get("fully_visible", False), pending_expansion=kind != "text",
                                 identity="content fingerprint; not an official WeChat message id")))
    return messages


def probe(config):
    result = _helper(config, "probe")
    if result.get("ok"):
        normalize_snapshot(dict(result, items=[]), config)
        result = dict(result)
        result.pop("items", None)
    return result


def capture_once(config):
    return normalize_snapshot(_helper(config, "capture"), config)


def expand_forward(config, message):
    meta = message.get("source_meta", {})
    if (message.get("group") != config["group"] or message.get("source") != "macos_ax" or
            message.get("kind") != "forward" or not meta.get("fully_visible") or
            type(meta.get("ax_row_index")) is not int):
        raise CaptureError("A fully visible bound macOS forward card is required")
    request = dict(process_id=meta["process_id"], row_index=meta["ax_row_index"],
                   text_sha256=hashlib.sha256(message["text"].encode()).hexdigest(),
                   detail_title=config.get("capture", {}).get("forward_window_title", "群聊的聊天记录"))
    result = _helper(config, "expand", request)
    binding = result.get("binding", {})
    if (result.get("ok") is not True or binding.get("parent_text_sha256") != request["text_sha256"] or
            binding.get("parent_row_index") != request["row_index"] or
            binding.get("process_id") != request["process_id"] or
            binding.get("new_window_observed") is not True or binding.get("restored") is not True):
        raise CaptureError(result.get("status", "macos_forward_binding_failed"))
    child_config = dict(config, group=result["window_title"])
    # Normalize the detail's explicitly bound list without inventing Windows AX ids.
    raw_detail = dict(result, group=result["window_title"], source="macos_ax",
                      group_binding=dict(method="exact_window_title"))
    rows = normalize_snapshot(raw_detail, child_config)
    for row in rows:
        stamp = re.search(r"\s+(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})\s*$", row["text"])
        if stamp:
            try:
                original = datetime.strptime(stamp.group(1), "%Y年%m月%d日 %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
                row.update(sent_at=original.isoformat(), text=row["text"][:stamp.start()].rstrip())
            except ValueError:
                pass
        row.update(id=message["id"] + ":" + row["id"], group=config["group"])
        row["source_meta"].update(parent_id=message["id"], source_group_verified=True)
    return dict(message, source="macos_ax_forward", children=rows, attachments=[], incomplete=True,
                source_group_verified=True, source_meta=dict(meta, history_complete=False,
                    identity_binding="verified_parent_open", binding=binding,
                    incomplete_reasons=["history_completeness_not_proven"]))
