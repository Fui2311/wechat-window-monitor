"""Compile/sign a new, separately identified capture App; never replace an existing App."""

import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from wechat_monitor import __version__
from wechat_tracker.paths import RESOURCE_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "Applications/WeChat Window Monitor Capture.app",
    )
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This build requires macOS and Apple Command Line Tools")
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        parser.error(
            "Output already exists; use a different --output path (existing App is preserved)"
        )
    if output.suffix != ".app":
        parser.error("Output must have .app extension")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="window-capture-build-", dir=output.parent
    ) as temporary:
        app = Path(temporary) / output.name
        binary = app / "Contents/MacOS/wechat-capture"
        binary.parent.mkdir(parents=True)
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-O",
                "-swift-version",
                "5",
                str(RESOURCE_ROOT / "mac-capture.swift"),
                "-o",
                str(binary),
            ],
            check=True,
        )
        metadata = {
            "CFBundleIdentifier": "local.wechat-window-monitor.capture",
            "CFBundleExecutable": "wechat-capture",
            "CFBundleName": "WeChat Window Monitor Capture",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,
            "NSAppleEventsUsageDescription": "Read the explicitly configured WeChat conversation window.",
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
        subprocess.run(
            [
                "codesign",
                "--force",
                "--sign",
                "-",
                "--identifier",
                "local.wechat-window-monitor.capture",
                str(app),
            ],
            check=True,
        )
        subprocess.run([str(binary), "self-test"], check=True)
        # Atomic same-volume promotion, refusing concurrent output creation.
        if output.exists():
            raise FileExistsError(output)
        shutil.move(str(app), str(output))
    print(f"Created {output}\nExecutable: {output}/Contents/MacOS/wechat-capture")


if __name__ == "__main__":
    main()
