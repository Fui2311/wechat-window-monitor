"""Keep shipped version labels consistent with pyproject.toml (read-only by default)."""
import argparse
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parent.parent
VERSION = r"(\d+\.\d+\.\d+)"
FIELDS = {
    "frontend/package.json": [r'("version": ")' + VERSION + r'(")'],
    "frontend/package-lock.json": [
        r'(\A\{\n  "name": "[^"]+",\n  "version": ")' + VERSION + r'(")',
        r'("packages": \{\n    "": \{\n      "name": "[^"]+",\n      "version": ")' + VERSION + r'(")',
    ],
    "wechat_monitor/__init__.py": [r'(__version__ = ")' + VERSION + r'(")'],
    "wechat_tracker/__init__.py": [r'(__version__ = ")' + VERSION + r'(")'],
    "wechat_tracker/resources/web/index.html": [r'(微信群讲座消息捕获与邮箱通知 · )' + VERSION + r'(</span>)'],
    "wechat_tracker/resources/mac-capture.swift": [r'("version": ")' + VERSION + r'(")'],
    "mac/build-console-launcher.py": [r'("CFBundleShortVersionString": ")' + VERSION + r'(")'],
    "README.md": [r'(\*\*正式版本：)' + VERSION + r'( · Python)'],
    "docs/deployment.md": [r'(部署、升级与发布（)' + VERSION + r'(）)',
                           r'(wechat_window_monitor-)' + VERSION + r'(-py3-none-any.whl)'],
    "docs/architecture.md": [r'(架构与扩展契约（)' + VERSION + r'(）)'],
}


def synchronize(root: Path, *, write: bool = False) -> tuple[str, list[str]]:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("This stable-release helper requires a version such as 1.2.3")
    updates = {}
    # Validate every target before writing any file, including when --write is used.
    for name, patterns in FIELDS.items():
        original = (root / name).read_text(encoding="utf-8")
        updated = original
        for pattern in patterns:
            if len(re.findall(pattern, updated)) != 1:
                raise ValueError(f"Expected exactly one version field: {name}: {pattern}")
            updated = re.sub(pattern, lambda match: match[1] + version + match[3], updated)
        if updated != original:
            updates[name] = updated
    if write:
        for name, content in updates.items():
            (root / name).write_text(content, encoding="utf-8")
    return version, list(updates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift without writing (default)")
    mode.add_argument("--write", action="store_true", help="update derived labels from pyproject.toml")
    args = parser.parse_args()
    try:
        version, changed = synchronize(ROOT, write=args.write)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Version synchronization failed: {error}\n")
    if changed:
        print(("Updated" if args.write else "Version drift in") + ": " + ", ".join(changed))
    else:
        print(f"Version labels match pyproject.toml: {version}")
    return int(bool(changed) and not args.write)


if __name__ == "__main__":
    raise SystemExit(main())
