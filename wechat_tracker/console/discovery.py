"""Discover managed monitor instances without reading stored credentials."""
import json
from pathlib import Path
from ..config import load_config
from ..paths import PROJECT_ROOT
from ..service import read_status
from .presentation import _sanitize_service
from .discovery_policy import ignored_discovery_path

def _list_services(active_config: dict, active_config_path: str) -> list[dict]:
    services = []
    seen_dirs = set()

    current_dir = Path(active_config["data_dir"]).resolve()
    current_status = read_status(active_config)
    services.append(_sanitize_service(current_status, {
        "data_dir": str(current_dir),
        "config_path": active_config_path,
        "group": active_config.get("group", ""),
        "is_current": True,
    }))
    seen_dirs.add(str(current_dir))

    state_root = PROJECT_ROOT / "state"
    search_dirs = [state_root]
    if current_dir.parent.is_dir() and current_dir.parent != state_root:
        search_dirs.append(current_dir.parent)
    if current_dir.parent.parent.name == "state":
        search_dirs.append(current_dir.parent.parent)

    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        # Group runs may keep data one level beneath their configuration folder.
        candidates = list(sdir.glob("*/service.json")) + list(sdir.glob("*/*/service.json"))
        for svc_file in candidates:
            sub = svc_file.parent
            if not sub.is_dir():
                continue
            sub_resolved = str(sub.resolve())
            if sub_resolved in seen_dirs:
                continue
            if ignored_discovery_path(sub.relative_to(sdir)):
                continue
            svc_file = sub / "service.json"
            if svc_file.is_file():
                try:
                    raw = json.loads(svc_file.read_text(encoding="utf-8"))
                    st = read_status({"data_dir": str(sub)})
                    services.append(_sanitize_service(st, {
                        "data_dir": str(sub),
                        "config_path": raw.get("config_path", ""),
                        "group": raw.get("group") or st.get("group") or sub.name,
                        "is_current": False,
                    }))
                    seen_dirs.add(sub_resolved)
                except Exception:
                    pass

    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    if launch_agents_dir.is_dir():
        import plistlib
        for plist_path in launch_agents_dir.glob("local.wechat-lecture-tracker.*.plist"):
            try:
                with plist_path.open("rb") as f:
                    plist = plistlib.load(f)
                args = plist.get("ProgramArguments", [])
                if "--config" in args:
                    cfg_idx = args.index("--config") + 1
                    if cfg_idx < len(args):
                        la_cfg_path = Path(args[cfg_idx]).resolve()
                        if la_cfg_path.is_file():
                            la_cfg = load_config(la_cfg_path)
                            la_dir = str(Path(la_cfg["data_dir"]).resolve())
                            if la_dir in seen_dirs:
                                for record in services:
                                    if record.get("data_dir") == la_dir:
                                        record["config_path"] = str(la_cfg_path)
                            if la_dir not in seen_dirs:
                                st = read_status(la_cfg)
                                services.append(_sanitize_service(st, {
                                    "data_dir": la_dir,
                                    "config_path": str(la_cfg_path),
                                    "group": la_cfg.get("group", ""),
                                    "is_current": (la_dir == str(current_dir)),
                                }))
                                seen_dirs.add(la_dir)
            except Exception:
                pass

    return services
