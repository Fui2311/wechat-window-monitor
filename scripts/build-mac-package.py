"""Build a separate Mac source installer ZIP with a closed file allowlist."""
import hashlib
import json
from pathlib import Path
import posixpath
import re
import runpy
import tempfile
import tomllib
import zipfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent


def check_document_links(contents):
    """Reject local inline documentation links that would be broken after unzip."""
    for name, data in contents.items():
        if not name.endswith(".md"):
            continue
        for target in re.findall(r"\]\(([^\s)]+)\)", data.decode("utf-8")):
            link = urlsplit(target)
            if link.scheme or link.netloc or not link.path:
                continue
            destination = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(link.path)))
            if destination not in contents:
                raise ValueError(f"Missing packaged documentation target: {name} -> {target}")


def build():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"] != "wechat-lecture-tracker":
        raise ValueError("Legacy lecture package builder; use python -m build for Window Monitor")
    version = project["version"]
    name = "WeChatLectureTracker-macOS-" + version
    files = {name: ROOT / name for name in ("README.md", "pyproject.toml", "CHANGELOG.md", "config.example.toml")}
    for doc_name in ("architecture.md", "deployment.md", "macos.md", "email.md", "llm-processing.md",
                 "automatic-forward.md", "macos-forward-background.md", "capture.md", "service.md",
                 "database-recovery.md", "console-usage.md", "github-reuse.md"):
        files["docs/" + doc_name] = ROOT / "docs" / doc_name
    for folder, extensions in (("mac", {".py", ".command", ".toml"}),
                               ("wechat_tracker", {".py", ".swift", ".jsonl", ".html", ".css", ".js", ".toml"}),
                               ("examples", {".jsonl"})):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in extensions and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path
    contents = {path: source.read_bytes().replace(b"\r\n", b"\n") for path, source in files.items()}
    check_document_links(contents)
    contents["PACKAGE-VALIDATION.json"] = json.dumps(dict(version=version, artifact="macOS source installer",
        build_checks=["full-file SHA256", "ZIP CRC", "closed file allowlist", "local documentation links"],
        macos_native_compile="verified separately; see GitHub release validation",
        macos_live_capture="requires a logged-in, authorized target desktop",
        smtp_credentials_included=False, windows_runtime_included=False), indent=2).encode()
    manifest = "".join(hashlib.sha256(data).hexdigest() + "  " + path + "\n" for path, data in sorted(contents.items()))
    contents["SHA256SUMS.txt"] = manifest.encode()
    output = ROOT / "dist" / (name + ".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name + "/" + path, date_time=(2026, 10, 15, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if path.endswith(".command") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    with tempfile.TemporaryDirectory(prefix="wechat-mac-package-") as temporary, zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        archive.extractall(temporary)
        verify = runpy.run_path(str(Path(temporary) / name / "mac/verify-package.py"))["verify"]
        count = verify(Path(temporary) / name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
    print(json.dumps(dict(package=str(output), sha256=digest, manifest_files=count, zip_crc=True,
                          native_mac_validation="pending"), indent=2))
    return output


if __name__ == "__main__":
    build()
