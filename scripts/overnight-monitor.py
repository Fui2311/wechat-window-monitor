"""Bounded, passive overnight telemetry; records metadata without sending mail."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time

import wechat_tracker
from wechat_tracker.config import load_config
from wechat_tracker.macos import capture_once, probe
from wechat_tracker.service import read_status


SERVICE_FIELDS = (
    "running", "status", "pid", "worker_pid", "worker_restarts", "snapshots",
    "observed_messages", "events_created", "last_capture_at", "last_error",
    "capture_gaps", "send_enabled", "email_sent", "email_failures",
    "heartbeat_at", "started_at", "send_ready", "email_state",
)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def command(arguments, timeout=10):
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout,
                          check=True).stdout


def power_state():
    assertions = command(["/usr/bin/pmset", "-g", "assertions"])
    counts = {}
    for line in assertions.splitlines():
        match = re.fullmatch(r"\s*(PreventUserIdleSystemSleep|PreventSystemSleep|PreventUserIdleDisplaySleep)\s+(\d+)\s*", line)
        if match:
            counts[match[1]] = int(match[2])
    owners = sorted({match.group(1) for match in
                     re.finditer(r"pid \d+\((Amphetamine|caffeinate)\)", assertions)})
    blockers = [dict(pid=int(m.group(1)), process=m.group(2), assertion=m.group(3)) for m in
                re.finditer(r"pid (\d+)\(([^)]+)\):[^\n]*?\b(PreventUserIdleSystemSleep|PreventSystemSleep|NoIdleSleepAssertion)\b", assertions)]
    registry = command(["/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"])
    lid = {m.group(1): m.group(2) == "Yes" for m in
           re.finditer(r'"(AppleClamshellState|AppleClamshellCausesSleep)"\s*=\s*(Yes|No)', registry)}
    battery = command(["/usr/bin/pmset", "-g", "batt"])
    live = command(["/usr/bin/pmset", "-g", "live"])
    disabled = re.search(r"^\s*SleepDisabled\s+([01])\s*$", live, re.M)
    source = re.search(r"drawing from '([^']+)'", battery)
    charge = re.search(r"(\d+)%;", battery)
    return dict(assertions=counts, owners=owners, sleep_prevention_owners=blockers, lid=lid,
                sleep_disabled=int(disabled.group(1)) if disabled else None,
                power_source=source.group(1) if source else None,
                battery_percent=int(charge.group(1)) if charge else None)


def sample(config, directory, *, deep=False):
    result = dict(sampled_at=stamp(), observer_pid=os.getpid(), deep_probe=deep,
                  ui_actions_performed=False, smtp_sent=False)
    try:
        state = read_status(config)
        result["service"] = {key: state.get(key) for key in SERVICE_FIELDS}
    except Exception as error:
        result["service_error_type"] = type(error).__name__
    try:
        result["power"] = power_state()
    except Exception as error:
        result["power_error_type"] = type(error).__name__
    if deep:
        try:
            result["desktop"] = json.loads(command([str(directory / "desktop-observer"), config["group"]]))
        except Exception as error:
            result["desktop_error_type"] = type(error).__name__
        try:
            raw = probe(config)
            result["probe"] = {key: raw.get(key) for key in
                               ("ok", "status", "group", "process_id", "message_count", "observed_at")}
            if raw.get("ok") is True:
                messages = capture_once(config)
                result["capture"] = dict(messages=len(messages),
                    kinds=dict(Counter(message["kind"] for message in messages)),
                    message_ids=[message["id"] for message in messages])
        except Exception as error:
            result["probe_error_type"] = type(error).__name__
    database = Path(config["data_dir"]) / "tracker.sqlite3"
    if database.is_file():
        try:
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                result["database"] = dict(counts={table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in ("messages", "events", "outbox", "capture_seen", "capture_gaps")})
                if deep:
                    result["database"]["integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
        except Exception as error:
            result["database_error_type"] = type(error).__name__
    result["finished_at"] = stamp()
    return result


def summarize(records, manifest):
    fresh, running = 0, 0
    transitions, gaps, previous = [], [], None
    for index, row in enumerate(records):
        state = row.get("service", {})
        running += state.get("running") is True
        captured = state.get("last_capture_at")
        age = None
        if captured:
            age = (datetime.fromisoformat(row["sampled_at"]) - datetime.fromisoformat(captured.replace("Z", "+00:00"))).total_seconds()
        fresh += state.get("status") == "monitoring" and age is not None and -2 <= age <= 45
        signature = (state.get("running"), state.get("status"), state.get("last_error"))
        if signature != previous:
            transitions.append(dict(at=row["sampled_at"], running=signature[0], status=signature[1], error=signature[2]))
            previous = signature
        if index:
            elapsed = (datetime.fromisoformat(row["sampled_at"]) - datetime.fromisoformat(records[index-1]["sampled_at"])).total_seconds()
            if elapsed > manifest["sample_seconds"] * 1.5:
                gaps.append(dict(after=records[index-1]["sampled_at"], before=row["sampled_at"], seconds=round(elapsed, 1)))
    deep = [row for row in records if row.get("deep_probe")]
    return dict(group=manifest["group"], deadline=manifest["deadline"], updated_at=stamp(),
        first_sample=records[0]["sampled_at"] if records else None,
        last_sample=records[-1]["sampled_at"] if records else None, samples=len(records),
        service_running_samples=running, fresh_capture_samples=fresh,
        fresh_capture_sample_percent=round(fresh * 100 / len(records), 2) if records else None,
        deep_probes=len(deep), deep_probe_successes=sum(row.get("probe", {}).get("ok") is True for row in deep),
        lid_closed_samples=sum(row.get("power", {}).get("lid", {}).get("AppleClamshellState") is True for row in records),
        screen_locked_deep_samples=sum(row.get("desktop", {}).get("screen_lock_flag") is True for row in deep),
        screen_lock_known_deep_samples=sum(type(row.get("desktop", {}).get("screen_lock_flag")) is bool for row in deep),
        errors=dict(Counter(row.get("service", {}).get("last_error") for row in records if row.get("service", {}).get("last_error"))),
        sampling_gaps=gaps, transitions=transitions,
        latest=records[-1] if records else None,
        scope="Loaded AX rows of the exact group; no new-message or complete-history guarantee.",
        causality_limit="A missing exact group window cannot by itself distinguish lock/display effects from a window not opened before the test.")


def read_records(directory):
    path = directory / "samples.jsonl"
    if not path.is_file():
        return []
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # A killed writer can leave one partial last line.
    return records


def run(directory, manifest, config):
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    deadline = datetime.fromisoformat(manifest["deadline"])
    save(directory / "observer.json", dict(pid=os.getpid(), started_at=stamp(),
        runtime_package=str(Path(wechat_tracker.__file__).resolve()), state="starting"))
    try:
        if not read_status(config).get("running"):
            raise RuntimeError("Start the independently supervised main service before observation")
        records = read_records(directory)
        next_deep = 0
        while not stop.is_set() and datetime.now(timezone.utc) < deadline:
            started = time.monotonic()
            deep = started >= next_deep
            row = sample(config, directory, deep=deep)
            records.append(row)
            with (directory / "samples.jsonl").open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            (directory / "samples.jsonl").chmod(0o600)
            report = summarize(records, manifest)
            report["phase"] = "observing"
            save(directory / "summary.json", report)
            save(directory / "observer.json", dict(pid=os.getpid(), state="observing", last_sample=row["sampled_at"],
                controls_service=False, runtime_package=str(Path(wechat_tracker.__file__).resolve())))
            if deep:
                next_deep = started + manifest["deep_probe_seconds"]
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            stop.wait(max(0, min(remaining, manifest["sample_seconds"] - (time.monotonic() - started))))
    except Exception as error:
        save(directory / "observer-error.json", dict(at=stamp(), error_type=type(error).__name__))
    finally:
        report = summarize(read_records(directory), manifest)
        report["phase"] = "completed" if datetime.now(timezone.utc) >= deadline else "stopped_early"
        save(directory / "summary.json", report)
        save(directory / "observer.json", dict(pid=os.getpid(), state=report["phase"], ended_at=stamp(),
            controls_service=False, service_left_running=True, runtime_package=str(Path(wechat_tracker.__file__).resolve())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sample", "run", "report"))
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    config = load_config(manifest["config"])
    if (config["capture"].get("auto_expand_forward") is not False or
            config["capture"].get("follow_tail", True) != manifest.get("follow_tail") or
            config["group"] != manifest["group"] or Path(config["data_dir"]).resolve() != directory / "data"):
        raise ValueError("Observation requires the exact approved main-group configuration")
    if args.action == "run":
        run(directory, manifest, config)
    elif args.action == "sample":
        print(json.dumps(sample(config, directory, deep=True), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize(read_records(directory), manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
