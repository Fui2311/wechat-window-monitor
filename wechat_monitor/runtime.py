"""Per-window lifecycle and retryable processing, with injected capture/model ports."""

import threading
from .recognition import LocalCore, SemanticCore
from .repository import now


class Runtime:
    def __init__(self, repository, capture, model):
        self.repository, self.capture, self.model = repository, capture, model
        self.lock = threading.RLock()
        self.capture_lock = threading.Lock()
        self.workers = {}
        self.states = {}

    def status(self, identity):
        with self.lock:
            task = self.workers.get(identity)
            running = bool(task and task[0].is_alive())
            state = dict(
                self.states.get(identity, {"status": "stopped", "error": ""}),
                running=running,
            )
            if running and task[1].is_set():
                state["status"] = "stopping"
            return state

    def busy(self, identity):
        return self.status(identity)["running"]

    def _set(self, identity, **values):
        with self.lock:
            self.states.setdefault(identity, {"status": "stopped", "error": ""}).update(
                values
            )

    def start(self, identity):
        with self.lock:
            if self.busy(identity):
                return self.status(identity)
            window = self.repository.window(identity)
            if not window or not window["active_rule"]:
                raise ValueError("请先生成或编辑规则，并明确启用一个版本")
            event = threading.Event()
            thread = threading.Thread(
                target=self._loop,
                args=(identity, event),
                daemon=True,
                name="window-" + identity[:8],
            )
            self.workers[identity] = (thread, event)
            self._set(identity, status="starting", error="")
            thread.start()
            return self.status(identity)

    def stop(self, identity, wait=0):
        with self.lock:
            task = self.workers.get(identity)
            if not task or not task[0].is_alive():
                self._set(identity, status="stopped", error="")
                return self.status(identity)
            task[1].set()
            self._set(identity, status="stopping")
        if wait:
            task[0].join(timeout=wait)
        return self.status(identity)

    def _loop(self, identity, stop):
        try:
            while not stop.is_set():
                window = self.repository.window(identity)
                try:
                    with self.capture_lock:
                        if stop.is_set():
                            break
                        messages = self.capture.read(window)
                    if stop.is_set():
                        break
                    self.repository.ingest(window, messages)
                    self._set(
                        identity,
                        status="monitoring",
                        error="",
                        last_capture=now(),
                        visible=len(messages),
                    )
                    self.process_pending(window, stop)
                except Exception:
                    # Provider/native exception text may contain data or secrets. Retain only safe diagnostics.
                    self._set(
                        identity,
                        status="waiting",
                        error="无法读取精确窗口，请检查名称、窗口可见性、原生程序及系统权限",
                    )
                stop.wait(window["interval"])
        finally:
            self._set(identity, status="stopped", error="")

    def process_pending(self, window, stop=None):
        rule = self.repository.rule(window["id"], window["active_rule"])
        if rule is None:
            raise ValueError("活动规则不存在")
        core = LocalCore() if window["mode"] == "local" else SemanticCore(self.model)
        errors = 0
        for item in self.repository.pending(window["id"]):
            if stop and stop.is_set():
                break
            try:
                result = core.recognize(item["payload"], rule["spec"])
                self.repository.finish(window["id"], item["id"], rule["id"], result)
            except Exception:
                errors += 1
                self.repository.finish(
                    window["id"],
                    item["id"],
                    rule["id"],
                    error="识别失败：请检查模型设置或规则；原文保留，可重试",
                )
        if errors:
            self._set(
                window["id"],
                status="recognition_error",
                error=f"{errors}条消息识别失败，可在记录页重试",
            )

    def close(self):
        with self.lock:
            tasks = list(self.workers.items())
        for identity, _ in tasks:
            self.stop(identity)
        for _, task in tasks:
            task[0].join(timeout=70)
