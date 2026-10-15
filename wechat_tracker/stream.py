"""Bounded native worker transport shared by Windows and macOS service runners."""
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading

from .capture import CaptureError


class NativeStream:
    def __init__(self, config):
        self.config = config
        self.process = None
        self.frames = queue.Queue(maxsize=4)
        self.closed = threading.Event()
        self.reader = None
        self.job = None

    def command(self):
        capture = self.config.get("capture", {})
        if sys.platform == "win32":
            executable = capture.get("powershell") or shutil.which("powershell.exe")
            if not executable:
                raise CaptureError("PowerShell is unavailable")
            script = Path(__file__).resolve().parent / "resources" / "stream-weixin.ps1"
            command = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-Group", self.config["group"],
                    "-ProcessName", capture.get("process_name", "Weixin"),
                    "-MaxItems", str(capture.get("max_items", 200)),
                    "-ResyncSeconds", str(self.config.get("service", {}).get("resync_seconds", 15))]
            if capture.get("follow_tail", True):
                command.append("-FollowTail")
            return command
        if sys.platform == "darwin":
            helper = capture.get("mac_helper")
            if not helper or not Path(helper).is_file():
                raise CaptureError("Build the macOS capture helper before starting the service")
            command = [str(helper), "stream", "--group", self.config["group"], "--resync-seconds",
                       str(self.config.get("service", {}).get("resync_seconds", 15)),
                       "--bundle-id", capture.get("bundle_id", "com.tencent.xinWeChat"),
                       "--header-id", capture.get("mac_header_identifier", ""),
                       "--list-id", capture.get("mac_list_identifier", "")]
            if capture.get("follow_tail", True):
                command.append("--follow-tail")
            return command
        raise CaptureError("Native capture requires Windows or macOS")

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if sys.platform == "win32":
            try:
                self.job = WindowsJob(self.process)
            except OSError:
                self.process.terminate()
                self.process.wait(timeout=5)
                raise CaptureError("Capture worker could not be bound to its parent lifetime") from None
        self.reader = threading.Thread(target=self._read, daemon=True, name="capture-frames")
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 2 * 1024 * 1024 or not line.endswith(b"\n"):
                    value = CaptureError("Native frame exceeds the 2 MiB protocol bound")
                else:
                    try:
                        value = json.loads(line.decode("utf-8-sig"))
                        if not isinstance(value, dict):
                            raise ValueError()
                    except (ValueError, UnicodeError):
                        value = CaptureError("Native worker emitted an invalid frame")
                while not self.closed.is_set():
                    try:
                        self.frames.put(value, timeout=0.5)
                        break
                    except queue.Full:
                        pass
        finally:
            if self.process and self.process.stdout:
                self.process.stdout.close()

    def next_frame(self, timeout=1):
        try:
            value = self.frames.get(timeout=timeout)
        except queue.Empty:
            if self.process is not None and self.process.poll() is not None:
                raise CaptureError(f"Native worker exited with code {self.process.returncode}")
            return None
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=2)
        if self.job:
            self.job.close()
            self.job = None

    @property
    def pid(self):
        return self.process.pid if self.process else None


class WindowsJob:
    """Kernel lifetime ownership prevents orphan UIA workers after parent crashes."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
