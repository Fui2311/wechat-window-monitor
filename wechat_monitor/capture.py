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
