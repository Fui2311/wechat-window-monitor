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
               "--list-id", options.get("mac_list_identifier", ""),
               "--forward-list-id", options.get("mac_forward_list_identifier", ""),
               "--forward-idle-seconds", str(options.get("forward_idle_seconds", 3)),
               "--forward-notice-seconds", str(options.get("forward_notice_seconds", 5)),
               "--forward-postpone-seconds", str(options.get("forward_postpone_seconds", 300)),
               "--forward-card-side", options.get("mac_forward_card_side", "right")]
    if action == "capture":
        if options.get("follow_tail", True):
            command.append("--follow-tail")
        if options.get("recover_window", False):
            command.append("--recover-window")
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
        ordinal = item.get("content_ordinal", counters[kind, text])
        if type(ordinal) is not int or not 0 <= ordinal < 200:
            raise CaptureError("macOS returned an invalid loaded-row occurrence")
        counters[kind, text] += 1
        identifier = "macax:" + _fingerprint(group, kind, text, ordinal)[4:]
        token = item.get("row_token")
        identity = "content fingerprint; not an official WeChat message id"
        if options.get("mac_message_identity", "content") == "ax_row":
            if not isinstance(token, str) or re.fullmatch(r"[0-9]{1,20}", token) is None:
                raise CaptureError("macOS row identity requires a verified AX element token")
            payload = [group, snapshot["process_id"], token, kind, text]
            identifier = "macax-row:" + hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
            identity = "AX row and process lifetime plus content; not an official WeChat message id"
        messages.append(dict(id=identifier, group=group,
                             sender=None, sent_at=None, observed_at=observed, text=text, kind=kind,
                             source="macos_ax", source_meta=dict(process_id=snapshot["process_id"],
                                 window_title=snapshot["window_title"], group_binding=dict(binding),
                                 ax_row_index=item.get("row_index", index),
                                 content_ordinal=ordinal,
                                 ax_row_token=token,
                                 fully_visible=item.get("fully_visible", False), pending_expansion=kind not in {"text", "file"},
                                 file_content_policy="metadata_only" if kind == "file" else None,
                                 identity=identity)))
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


def _forward_request(config, message, *, require_fully_visible=True):
    meta = message.get("source_meta", {})
    options = config.get("capture", {})
    binding = meta.get("group_binding", {})
    exact_window = meta.get("window_title") == config["group"]
    exact_header = (bool(options.get("mac_header_identifier")) and
                    binding.get("method") == "explicit_header_identifier" and
                    binding.get("header_identifier") == options["mac_header_identifier"] and
                    binding.get("header_value") == config["group"])
    if (message.get("group") != config["group"] or message.get("source") != "macos_ax" or
            message.get("kind") != "forward" or
            (require_fully_visible and meta.get("fully_visible") is not True) or
            not isinstance(message.get("id"), str) or not message["id"] or
            not isinstance(message.get("text"), str) or _classify(message["text"], "AXRow") != "forward" or
            type(meta.get("process_id")) is not int or meta["process_id"] <= 0 or
            type(meta.get("ax_row_index")) is not int or meta["ax_row_index"] < 0 or
            not (exact_window or exact_header)):
        raise CaptureError("A freshly bound macOS forward card is required; expansion also requires full visibility")
    return dict(process_id=meta["process_id"], row_index=meta["ax_row_index"],
                   text_sha256=hashlib.sha256(message["text"].encode()).hexdigest(),
                   detail_title=config.get("capture", {}).get("forward_window_title", "群聊的聊天记录"))


def expand_forward(config, message):
    mode = config.get("capture", {}).get("mac_forward_interaction", "ax_press")
    if mode not in {"ax_press", "activate_click"}:
        raise CaptureError("Unknown macOS forward interaction mode")
    request = _forward_request(config, message, require_fully_visible=mode != "activate_click")
    if mode == "activate_click" and message["source_meta"]["window_title"] != config["group"]:
        raise CaptureError("Activated expansion requires the exact independent group window")
    result = _helper(config, "expand-activated" if mode == "activate_click" else "expand", request)
    binding = result.get("binding", {})
    if (result.get("ok") is not True or result.get("process_id") != request["process_id"] or
            result.get("window_title") != request["detail_title"] or
            binding.get("parent_text_sha256") != request["text_sha256"] or
            binding.get("parent_row_index") != request["row_index"] or
            binding.get("process_id") != request["process_id"] or
            binding.get("new_window_observed") is not True or binding.get("restored") is not True):
        raise CaptureError(result.get("status", "macos_forward_binding_failed"))
    if mode == "activate_click":
        foreground = binding.get("foreground_pid_before")
        if (binding.get("interaction_method") != mode or binding.get("activation_requested") is not True or
                binding.get("global_input_used") is not True or binding.get("foreground_restored") is not True or
                binding.get("cursor_restored") is not True or
                type(foreground) is not int or foreground <= 0 or binding.get("foreground_pid_after") != foreground):
            raise CaptureError("macos_foreground_restore_not_verified")
        if (binding.get("notice_completed") is not True or
                type(binding.get("notice_seconds")) is not int or
                binding["notice_seconds"] < max(5, config.get("capture", {}).get("forward_notice_seconds", 5))):
            raise CaptureError("macos_forward_notice_not_verified")
    return _normalize_forward(config, message, result, verified=True)


def read_open_forward(config, message):
    """Read user-opened detail, retaining explicit manual source attribution."""
    # Passive reading does not click the parent, so visual clipping is harmless.
    # The native helper still rechecks the exact group, row, PID and text hash.
    request = _forward_request(config, message, require_fully_visible=False)
    result = _helper(config, "read-forward", request)
    binding = result.get("binding", {})
    if (result.get("ok") is not True or result.get("process_id") != request["process_id"] or
            result.get("window_title") != request["detail_title"] or
            binding.get("method") != "explicit_manual_parent_id" or binding.get("read_only") is not True or
            binding.get("ui_actions_performed") is not False or binding.get("detail_window_count") != 1 or
            binding.get("process_id") != request["process_id"] or
            binding.get("parent_row_index") != request["row_index"] or
            binding.get("parent_text_sha256") != request["text_sha256"]):
        raise CaptureError(result.get("status", "macos_manual_forward_binding_failed"))
    return _normalize_forward(config, message, result, verified=False)


def _normalize_forward(config, message, result, *, verified):
    meta = message["source_meta"]
    binding = result["binding"]
    child_config = dict(config, group=result["window_title"])
    # Normalize the detail's explicitly bound list without inventing Windows AX ids.
    raw_detail = dict(result, group=result["window_title"], source="macos_ax",
                      group_binding=dict(method="exact_window_title"))
    rows = normalize_snapshot(raw_detail, child_config)
    for row in rows:
        raw_text = row["text"]
        stamp = re.search(r"\s+(\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2})\s*$", row["text"])
        if stamp:
            try:
                original = datetime.strptime(stamp.group(1), "%Y年%m月%d日 %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
                row.update(sent_at=original.isoformat(), text=row["text"][:stamp.start()].rstrip())
            except ValueError:
                pass
        row.update(id=message["id"] + ":" + row["id"], group=config["group"])
        row["source_meta"].update(parent_id=message["id"], source_group_verified=verified,
                                  raw_text=raw_text, timestamp_basis="trailing_accessible_full_date_time" if row["sent_at"] else None)
    return dict(message, source="macos_ax_forward", children=rows, attachments=[], incomplete=True,
                source_group_verified=verified, source_meta=dict(meta, history_complete=False,
                    identity_binding="verified_parent_open" if verified else "explicit_manual_parent_id", binding=binding,
                    forward_diagnostics=result.get("diagnostics", {}),
                    detail_read_only=not verified,
                    incomplete_reasons=["history_completeness_not_proven"]))
