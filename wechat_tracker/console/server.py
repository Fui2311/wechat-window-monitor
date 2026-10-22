"""Local HTTP server lifecycle, independent of monitor lifecycle."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import time
import webbrowser
from ..config import load_config, resolve_config_path
from .handler import WebControlHandler

class WebControlServer(ThreadingHTTPServer):
    # Finish in-flight requests before run_web closes the server and exits.
    daemon_threads = False

    def __init__(self, server_address, RequestHandlerClass, config_path: str | Path):
        self.config_path = str(Path(config_path).resolve())
        self.config = load_config(self.config_path)
        self.shutdown_requested = threading.Event()
        super().__init__(server_address, RequestHandlerClass)


def run_web(config_path: str | Path | None = None, host: str = "127.0.0.1", port: int = 8088, open_browser: bool = False):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("控制台仅允许监听本机 127.0.0.1 或 localhost")
    config_path = resolve_config_path(config_path)
    server = WebControlServer((host, port), WebControlHandler, config_path)
    url = f"http://{host}:{port}/"
    print("微信群讲座追踪系统 · 原生控制页面已启动:")
    print(f"  地址: {url}")
    print(f"  配置: {config_path}")
    print(f"  监测群: {server.config.get('group', '未配置')}")
    print(f"  数据目录: {server.config.get('data_dir', '未配置')}")
    print("按 Ctrl+C 可停止网页控制台服务\n")

    if open_browser:
        def _open():
            time.sleep(0.6)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止控制台服务...")
    finally:
        server.server_close()
        print("控制台服务已停止。")
    return 0
