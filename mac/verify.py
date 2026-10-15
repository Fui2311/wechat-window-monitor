"""Mac acceptance checks. Synthetic replay and actual AX readiness stay distinct."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

# Support the shipped source tree as well as the installed venv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def replay(config, fixtures):
    from wechat_tracker.engine import Engine
    from wechat_tracker.store import Store
    with tempfile.TemporaryDirectory(prefix="wechat-acceptance-") as temporary:
        store = Store(Path(temporary) / "tracker.sqlite3")
        try:
            engine = Engine(store, config)
            records = [json.loads(line) for line in (fixtures / "screenshot.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            now = datetime.fromisoformat("2026-10-15T13:00:00+08:00")
            for _ in range(2):
                for record in records:
                    engine.ingest(record, now)
            assert len(store.events()) == 1 and len(store.jobs()) == 3
            delivery = engine.dispatch(now=now, send=False)
            assert delivery and all(row["status"] == "preview" for row in delivery)
            assert all(len(row["body"].splitlines()) == 3 for row in delivery)
            assert {job["due_at"] for job in store.jobs() if job["reason"] == "reminder"} == {
                "2026-10-16T06:30:00+00:00", "2026-10-17T05:30:00+00:00"}
            return dict(events=1, jobs=3, duplicate_replay=True, smtp_sent=False)
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--offline", action="store_true", help="only synthetic core checks; no claim about AX")
    parser.add_argument("--observe-seconds", type=int, default=0, help="observe an already started service")
    args = parser.parse_args()
    from wechat_tracker.config import load_config
    config = load_config(args.config)
    report = dict(platform=platform.platform(), architecture=platform.machine(), python=platform.python_version(),
                  synthetic_replay=replay(config, Path(__file__).resolve().parent.parent / "examples"))
    if not args.offline:
        if sys.platform != "darwin":
            raise RuntimeError("Native checks require macOS; use --offline for the synthetic core only")
        helper = config["capture"]["mac_helper"]
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(Path(helper).parents[2])], check=True)
        native = subprocess.run([helper, "self-test"], capture_output=True, check=True, timeout=10)
        report["native_helper"] = json.loads(native.stdout)
        from wechat_tracker.macos import probe
        result = probe(config)
        report["native_probe"] = result
        if not result.get("ok"):
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise RuntimeError("Native probe is not ready: " + str(result.get("status")))
        from wechat_tracker.credentials import apply_credentials
        report["email_configured"] = apply_credentials(config)
        if args.observe_seconds:
            if not 15 <= args.observe_seconds <= 86400:
                raise ValueError("Observation must be 15..86400 seconds")
            from wechat_tracker.service import read_status
            deadline = time.monotonic() + args.observe_seconds
            samples = 0
            instance = None
            while True:
                health = read_status(config)
                if not health.get("running") or health.get("status") != "monitoring":
                    raise RuntimeError("The monitoring service is not ready")
                if instance is not None and health["instance"] != instance:
                    raise RuntimeError("Service restarted during observation; inspect health and capture gaps")
                instance = health["instance"]
                heartbeat = datetime.fromisoformat(health["last_capture_at"].replace("Z", "+00:00"))
                if (datetime.now(heartbeat.tzinfo) - heartbeat).total_seconds() > 90:
                    raise RuntimeError("Capture heartbeat is stale")
                samples += 1
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(5, deadline - time.monotonic()))
            report["service_observation"] = dict(seconds=args.observe_seconds, samples=samples, unchanged_instance=True,
                                                 history_complete=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        raise SystemExit("Acceptance check failed: " + str(error))
