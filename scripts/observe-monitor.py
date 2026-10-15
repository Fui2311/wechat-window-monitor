"""Read-only soak observation of an existing monitor. Never starts/stops it."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def read_sample(config, psutil):
    from wechat_tracker.service import read_status
    health = read_status(config)
    sample = dict(at=utc_now(), running=health.get("running"), status=health.get("status"),
                  instance=health.get("instance"), capture_gaps=health.get("capture_gaps"),
                  worker_restarts=health.get("worker_restarts"), email_state=health.get("email_state"),
                  send_ready=health.get("send_ready"), issues=[])
    cpu = {}
    if not health.get("running"):
        sample["issues"].append("service_not_responding")
    else:
        process = psutil.Process(health["pid"])
        # The authenticated control response and process command both bind this
        # observation; an unrelated process with a reused PID is never accepted.
        if "wechat_tracker" not in process.cmdline():
            raise RuntimeError("Service process identity is not verified")
        processes = [process] + process.children(recursive=True)
        rss = 0
        for item in processes:
            try:
                cpu[(item.pid, item.create_time())] = sum(item.cpu_times()[:2])
                rss += item.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        sample.update(service_pid=process.pid, tree_mib=round(rss / 2 ** 20, 3), process_count=len(cpu))
        if health.get("status") != "monitoring":
            sample["issues"].append("capture_not_ready")
        observed = health.get("last_capture_at")
        if observed:
            stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            sample["capture_age_seconds"] = round(age, 2)
            if age > 90 or age < -5:
                sample["issues"].append("capture_heartbeat_stale_or_clock_changed")
        else:
            sample["issues"].append("capture_heartbeat_missing")
    database = Path(config["data_dir"]) / "tracker.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
        sample["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
        sample["counts"] = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                            for name in ("messages", "events", "outbox", "capture_gaps")}
    if sample["integrity"] != "ok":
        sample["issues"].append("database_integrity_failed")
    return sample, cpu


def observe(config, output, seconds, interval, psutil):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "summary.json").exists():
        raise ValueError("Use a new observation directory; previous evidence will not be overwritten")
    summary = dict(status="running", observer_pid=os.getpid(), observer_created_at=psutil.Process().create_time(),
                   started_at=utc_now(), requested_seconds=seconds, interval_seconds=interval, samples=0,
                   unhealthy_samples=0, service_instance_changes=0, total_cpu_seconds=0,
                   capture_gap_increase=0, initial_capture_gaps=None, observer_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   max_tree_mib=0, max_single_core_cpu_percent=0, max_capture_age_seconds=0,
                   resource_limits=dict(tree_mib=200, average_single_core_cpu_percent=1),
                   actual_email_delivery_verified=False,
                   coverage="sampled live control and loaded viewport heartbeat; no complete-message or zero-loss proof")
    save(output / "summary.json", summary)
    started = time.monotonic()
    deadline = started + seconds
    previous_cpu, previous_at, previous_instance = None, None, None
    try:
        while True:
            now = time.monotonic()
            try:
                sample, cpu = read_sample(config, psutil)
            except (OSError, ValueError, RuntimeError, sqlite3.Error, psutil.Error) as error:
                sample, cpu = dict(at=utc_now(), issues=[type(error).__name__]), {}
            if previous_cpu is not None:
                duration = max(0.001, now - previous_at)
                used = sum(max(0, value - previous_cpu.get(identity, value)) for identity, value in cpu.items())
                # New processes are excluded from the delta; report this limit
                # rather than treating PID reuse as CPU consumption.
                sample["single_core_cpu_percent"] = round(used / duration * 100, 4)
                summary["total_cpu_seconds"] += used
                summary["max_single_core_cpu_percent"] = max(summary["max_single_core_cpu_percent"], sample["single_core_cpu_percent"])
            if previous_instance and sample.get("instance") and previous_instance != sample["instance"]:
                summary["service_instance_changes"] += 1
            previous_cpu, previous_at = cpu, now
            previous_instance = sample.get("instance") or previous_instance
            summary["samples"] += 1
            summary["unhealthy_samples"] += bool(sample["issues"])
            summary["max_tree_mib"] = max(summary["max_tree_mib"], sample.get("tree_mib", 0))
            summary["max_capture_age_seconds"] = max(summary["max_capture_age_seconds"], sample.get("capture_age_seconds", 0))
            if sample.get("capture_gaps") is not None:
                if summary["initial_capture_gaps"] is None:
                    summary["initial_capture_gaps"] = sample["capture_gaps"]
                summary["capture_gap_increase"] = max(summary["capture_gap_increase"], sample["capture_gaps"] - summary["initial_capture_gaps"])
            summary["latest_sample"] = sample
            summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
            summary["average_single_core_cpu_percent"] = round(summary["total_cpu_seconds"] / max(0.001, summary["elapsed_seconds"]) * 100, 4)
            with (output / "samples.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
            save(output / "summary.json", summary)
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0, min(interval, deadline - time.monotonic())))
    except BaseException:
        summary.update(status="interrupted", ended_at=utc_now(), passed_monitoring_observation=False)
        save(output / "summary.json", summary)
        raise
    summary.update(status="completed", ended_at=utc_now(),
                   cpu_boundary="live process deltas only; new/exited child CPU may be omitted",
                   passed_monitoring_observation=bool(summary["unhealthy_samples"] == 0 and
                       summary["service_instance_changes"] == 0 and summary["capture_gap_increase"] == 0 and summary["max_tree_mib"] < 200 and
                       summary["average_single_core_cpu_percent"] < 1))
    save(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=int, default=86400)
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 86400 or not 1 <= args.interval <= 300:
        raise ValueError("seconds must be 5..86400 and interval 1..300")
    try:
        import psutil
    except ImportError:
        raise RuntimeError("This optional diagnostic uses psutil; install it in the diagnostic Python environment") from None
    from wechat_tracker.config import load_config
    report = observe(load_config(args.config), Path(args.output).resolve(), args.seconds, args.interval, psutil)
    print(json.dumps({key: report[key] for key in ("status", "samples", "unhealthy_samples", "passed_monitoring_observation")}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("Observation failed: " + str(error), file=sys.stderr)
        raise SystemExit(2)
