"""OS-held workspace lease: stale files never imply a live process."""

import os
from pathlib import Path


class WorkspaceLock:
    def __init__(self, root):
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (root / ".process-lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b" ")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError(
                "该数据目录已有进程运行，请使用原控制台或另一数据目录"
            ) from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt

            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
