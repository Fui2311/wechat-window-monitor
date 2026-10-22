"""Use cases: optimistic config saves, explicit rule generation/activation and safe tests."""

import threading
from .capture import ExactWindowCapture
from .config import window_config
from .repository import Repository
from .model import ModelSettings, ChatClient
from .runtime import Runtime
from .recognition import RuleGenerator, LocalCore, SemanticCore
from .recognition.schema import validate_rule
from wechat_tracker.domain.messages import validate_message


class Application:
    def __init__(self, root, *, model=None, capture=None):
        self.repo = Repository(root)
        self.settings = ModelSettings(self.repo.root)
        self.model = model or ChatClient(self.settings)
        self.capture = capture or ExactWindowCapture()
        self.runtime = Runtime(self.repo, self.capture, self.model)
        self.lock = threading.RLock()

    def require_window(self, identity):
        window = self.repo.window(identity)
        if window is None:
            raise ValueError("窗口不存在")
        return window

    def windows(self):
        return [
            dict(
                w,
                runtime=self.runtime.status(w["id"]),
                counts=self.repo.counts(w["id"]),
            )
            for w in self.repo.list_windows()
        ]

    def save_window(self, body, identity=None):
        cfg = window_config(body)
        with self.lock:
            if identity and self.runtime.busy(identity):
                raise ValueError("请先停止该窗口监控，等待停止完成再修改配置")
            if (
                identity
                and self.require_window(identity)["name"] != cfg["name"]
                and self.repo.counts(identity)
            ):
                raise ValueError(
                    "已有采集记录的窗口不能改名，请添加新窗口以保留来源隔离"
                )
            return self.repo.save_window(cfg, identity, body.get("revision"))

    def generate(self, identity):
        window = self.require_window(identity)
        spec = RuleGenerator(self.model).generate(window["target"], window["mode"])
        with self.lock:
            current = self.require_window(identity)
            if any(current[k] != window[k] for k in ("target", "mode", "revision")):
                raise ValueError("生成期间配置已变化，请按最新目标重新生成")
            return self.repo.save_rule(identity, spec)

    def save_rule(self, identity, spec):
        with self.lock:
            window = self.require_window(identity)
            spec = validate_rule(spec, mode=window["mode"], target=window["target"])
            return self.repo.save_rule(identity, spec, "edited")

    def activate(self, identity, rule_id):
        with self.lock:
            window = self.require_window(identity)
            if self.runtime.busy(identity):
                raise ValueError("请先停止监控，再启用新的规则版本")
            rule = self.repo.rule(identity, rule_id)
            if rule is None:
                raise ValueError("规则版本不存在")
            validate_rule(rule["spec"], mode=window["mode"], target=window["target"])
            self.repo.activate(identity, rule_id)
            return self.require_window(identity)

    def start(self, identity):
        with self.lock:
            return self.runtime.start(identity)

    def test(self, identity, spec, message):
        window = self.require_window(identity)
        rule = validate_rule(spec, mode=window["mode"], target=window["target"])
        validate_message(message, window["name"])
        core = LocalCore() if window["mode"] == "local" else SemanticCore(self.model)
        return core.recognize(message, rule)

    def probe(self, identity):
        window = self.require_window(identity)
        with self.runtime.capture_lock:
            result = self.capture.probe(window)
        # Native probes may include verbose metadata; export only minimal readiness.
        return {
            "ok": result.get("ok") is True,
            "status": result.get("status", "unknown"),
            "window_name": window["name"],
            "message_count": result.get("message_count", 0),
        }

    def close(self):
        self.runtime.close()
