"""Map exact window names onto native AX/UIA contracts, with no lecture policy."""

import sys
from pathlib import Path
from wechat_tracker.capture import capture_once, probe


def native_config(window):
    return {
        "group": window["name"],
        "capture": dict(window["capture"], follow_tail=False, recover_window=False),
    }


class ExactWindowCapture:
    def expand(self, window, message):
        config = native_config(window)
        config["capture"].update(
            mac_forward_interaction="activate_click",
            forward_notice_seconds=5,
            forward_timeout_seconds=45,
        )
        # Refresh the row/PID binding immediately before interacting with the card.
        candidates = [
            m for m in capture_once(config) if m.get("id") == message.get("id")
        ]
        if len(candidates) != 1:
            raise ValueError("转发卡片已不在当前可读窗口，请显示原卡片后重试")
        if window["capture"]["source"] == "macos_ax":
            from wechat_tracker.macos import expand_forward
        else:
            from wechat_tracker.capture import expand_forward
        return expand_forward(config, candidates[0])

    def read(self, window):
        return capture_once(native_config(window))

    def probe(self, window):
        return probe(native_config(window))


def default_source():
    return (
        "macos_ax"
        if sys.platform == "darwin"
        else "windows_uia"
        if sys.platform == "win32"
        else "jsonl"
    )


def default_capture():
    value = {"source": default_source()}
    if value["source"] == "macos_ax":
        value["mac_helper"] = str(
            Path.home()
            / "Applications/WeChat Window Monitor Capture.app/Contents/MacOS/wechat-capture"
        )
    return value
