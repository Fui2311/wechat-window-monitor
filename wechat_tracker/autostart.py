"""User-session supervision; GUI capture must never run in Windows session 0."""
import csv
from datetime import datetime, timedelta
import hashlib
import io
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def task_name(config):
    return "WeChatLectureTracker-" + hashlib.sha256(config["group"].encode()).hexdigest()[:8]


def _operation_error(arguments, process):
    tool = Path(arguments[0]).name
    action = arguments[1] if len(arguments) > 1 else ""
    detail = getattr(process, "stderr", b"").decode("utf-8", errors="replace").strip()
    message = f"{tool} {action} failed (exit {process.returncode})"
    if detail:
        message += ": " + detail[:500]
    return RuntimeError(message)


def _run(arguments, *, check=True):
    process = subprocess.run(arguments, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and process.returncode:
        raise _operation_error(arguments, process)
    return process


def _mac_loaded(service):
    arguments = ["/bin/launchctl", "print", service]
    result = _run(arguments, check=False)
    if result.returncode == 0:
        return True
    if result.returncode in {3, 113}:
        return False
    raise _operation_error(arguments, result)


def _mac_bootout(service, timeout=5):
    """Confirm removal even when launchd reports an already-exited process."""
    arguments = ["/bin/launchctl", "bootout", service]
    result = _run(arguments, check=False)
    deadline = time.monotonic() + timeout
    while True:
        loaded = _mac_loaded(service)
        if not loaded:
            return
        # ESRCH and EINPROGRESS can accompany an asynchronous launchd removal.
        if result.returncode not in {0, 3, 36}:
            raise _operation_error(arguments, result)
        if time.monotonic() >= deadline:
            if result.returncode:
                raise _operation_error(arguments, result)
            raise RuntimeError("launchctl bootout did not finish removing the LaunchAgent")
        time.sleep(0.05)


def windows_task(config_path, config, action, *, send=False):
    name = task_name(config)
    description = "Managed by wechat-lecture-tracker; config=" + str(Path(config_path).resolve())
    query = _run(["schtasks.exe", "/Query", "/TN", name, "/XML"], check=False)
    exists = query.returncode == 0
    if exists:
        # schtasks outputs UTF-16 on some Windows installations.
        raw = query.stdout
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw[:100] else "utf-8-sig"
        try:
            existing = ET.fromstring(raw.decode(encoding))
            recorded = existing.find("{*}RegistrationInfo/{*}Description")
            if recorded is None or recorded.text != description:
                raise ValueError()
        except (ValueError, UnicodeError, ET.ParseError):
            raise RuntimeError("The existing task is not owned by this configuration; refusing to replace it") from None
    if action == "status":
        return {"installed": exists, "task_name": name}
    if action == "install":
        identity = _run(["whoami.exe", "/user", "/fo", "csv", "/nh"]).stdout.decode(errors="replace")
        sid = next(csv.reader(io.StringIO(identity)))[1]
        namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ET.register_namespace("", namespace)
        def element(parent, tag, text=None, **attributes):
            result = ET.SubElement(parent, "{" + namespace + "}" + tag, attributes)
            result.text = text
            return result
        task = ET.Element("{" + namespace + "}Task", {"version": "1.4"})
        registration = element(task, "RegistrationInfo")
        element(registration, "Description", description)
        trigger = element(element(task, "Triggers"), "LogonTrigger")
        element(trigger, "Enabled", "true")
        element(trigger, "UserId", sid)
        # RestartOnFailure alone does not recover a force-terminated pythonw on
        # every Windows build. An independent timer checks once each minute;
        # IgnoreNew and the OS lock exclude duplicate monitors.
        timer = element(task.find("{*}Triggers"), "TimeTrigger")
        repetition = element(timer, "Repetition")
        element(repetition, "Interval", "PT1M")
        element(repetition, "StopAtDurationEnd", "false")
        element(timer, "StartBoundary", (datetime.now().astimezone() + timedelta(minutes=1)).isoformat(timespec="seconds"))
        element(timer, "Enabled", "true")
        principal = element(element(task, "Principals"), "Principal", id="Author")
        element(principal, "UserId", sid)
        element(principal, "LogonType", "InteractiveToken")
        element(principal, "RunLevel", "LeastPrivilege")
        settings = element(task, "Settings")
        for key, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                           ("StopIfGoingOnBatteries", "false"), ("AllowHardTerminate", "true"),
                           ("StartWhenAvailable", "true"), ("AllowStartOnDemand", "true"),
                           ("Enabled", "true"), ("Hidden", "false"), ("ExecutionTimeLimit", "PT0S"), ("Priority", "7")):
            element(settings, key, value)
        restart = element(settings, "RestartOnFailure")
        element(restart, "Interval", "PT1M")
        element(restart, "Count", "999")
        execute = element(element(task, "Actions", Context="Author"), "Exec")
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        element(execute, "Command", str(pythonw) if pythonw.is_file() else sys.executable)
        arguments = ["-m", "wechat_tracker", "--config", str(Path(config_path).resolve()), "service", "run"]
        if send:
            arguments.append("--send")
        element(execute, "Arguments", subprocess.list2cmdline(arguments))
        element(execute, "WorkingDirectory", str(Path(__file__).resolve().parent.parent))
        target = Path(config["data_dir"]) / "startup-task.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(task).write(target, encoding="utf-16", xml_declaration=True)
        _run(["schtasks.exe", "/Create", "/TN", name, "/XML", str(target), "/F"])
    elif action == "start":
        if not exists:
            raise RuntimeError("Install the login task before starting it")
        _run(["schtasks.exe", "/Change", "/TN", name, "/ENABLE"])
        _run(["schtasks.exe", "/Run", "/TN", name])
    elif action == "pause":
        if exists:
            _run(["schtasks.exe", "/Change", "/TN", name, "/DISABLE"])
    elif action == "uninstall":
        if exists:
            _run(["schtasks.exe", "/Delete", "/TN", name, "/F"])
    else:
        raise ValueError("Unsupported startup action")
    return {"installed": action != "uninstall", "task_name": name, "action": action}


def manage_startup(config_path, config, action, *, send=False):
    if sys.platform == "win32":
        return windows_task(config_path, config, action, send=send)
    if sys.platform == "darwin":
        return mac_agent(config_path, config, action, send=send)
    raise ValueError("This startup manager is not yet available on this platform")


def mac_agent(config_path, config, action, *, send=False):
    label = "local.wechat-lecture-tracker." + task_name(config).split("-")[-1]
    target = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
    domain = "gui/" + str(os.getuid())
    service = domain + "/" + label
    config_path = str(Path(config_path).resolve())
    if target.exists():
        with target.open("rb") as handle:
            existing = plistlib.load(handle)
        arguments = existing.get("ProgramArguments", [])
        if "--config" not in arguments or arguments[arguments.index("--config") + 1] != config_path:
            raise RuntimeError("The existing LaunchAgent belongs to another configuration")
    loaded = _mac_loaded(service)
    if action == "status":
        return {"installed": target.is_file(), "loaded": loaded, "label": label}
    if action in {"install", "configure"}:
        if loaded:
            _mac_bootout(service)
        arguments = [sys.executable, "-m", "wechat_tracker", "--config", config_path, "service", "run"]
        if send:
            arguments.append("--send")
        payload = dict(Label=label, ProgramArguments=arguments, RunAtLoad=True,
                       KeepAlive={"SuccessfulExit": False}, ThrottleInterval=10,
                       ProcessType="Background", WorkingDirectory=str(Path(__file__).resolve().parent.parent),
                       StandardOutPath="/dev/null", StandardErrorPath="/dev/null")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(plistlib.dumps(payload))
        temporary.chmod(0o600)
        os.replace(temporary, target)
        if action == "install":
            _run(["/bin/launchctl", "bootstrap", domain, str(target)])
    elif action == "start":
        if not target.is_file():
            raise RuntimeError("Install the LaunchAgent before starting it")
        expected = [sys.executable, "-m", "wechat_tracker", "--config", config_path, "service", "run"]
        if send:
            expected.append("--send")
        if (existing.get("ProgramArguments") != expected or
                existing.get("WorkingDirectory") != str(Path(__file__).resolve().parent.parent)):
            return mac_agent(config_path, config, "install", send=send)
        if not loaded:
            _run(["/bin/launchctl", "bootstrap", domain, str(target)])
        _run(["/bin/launchctl", "kickstart", service])
    elif action == "pause":
        if loaded:
            _mac_bootout(service)
    elif action == "uninstall":
        if loaded:
            _mac_bootout(service)
        target.unlink(missing_ok=True)
    else:
        raise ValueError("Unsupported LaunchAgent action")
    return {"installed": target.is_file(), "label": label, "action": action}
