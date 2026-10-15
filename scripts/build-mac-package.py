"""Build a separate Mac source installer ZIP with a closed file allowlist."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def build():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    name = "WeChatLectureTracker-macOS-" + version
    files = {"README.md": ROOT / "docs/macos.md", "pyproject.toml": ROOT / "pyproject.toml"}
    for folder, extensions in (("mac", {".py", ".command", ".toml"}),
                               ("wechat_tracker", {".py", ".swift", ".jsonl"}),
                               ("examples", {".jsonl"})):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in extensions and "__pycache__" not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path
    contents = {path: source.read_bytes().replace(b"\r\n", b"\n") for path, source in files.items()}
    contents["PACKAGE-VALIDATION.json"] = json.dumps(dict(version=version, artifact="macOS source installer",
        windows_checks=["full-file SHA256", "ZIP CRC", "Python syntax", "synthetic replay"],
        macos_native_compile="pending Mac execution", macos_live_capture="pending Mac execution",
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
