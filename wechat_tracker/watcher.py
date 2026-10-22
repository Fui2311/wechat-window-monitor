"""Bounded forward enrichment with a restart-safe cache and retry backoff."""
from datetime import datetime, timedelta, timezone
import json

from .capture import CaptureError, expand_forward
from .engine import parse_time, utc_text, validate_message
from .store import Store


def enrich_forwards(messages: list[dict], store: Store, config: dict, *,
                    include_existing=False, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    now = now or datetime.now(timezone.utc)
    group = config["group"]
    # Validate the complete batch before any UI input or database mutation.
    for message in messages:
        validate_message(message, group)
    first = not store.db.execute("SELECT 1 FROM capture_state WHERE group_name=?", (group,)).fetchone()
    if not config.get("capture", {}).get("auto_expand_forward", False):
        return messages, []
    if first and not include_existing:
        with store.db:
            for message in messages:
                if message.get("kind") == "forward" and message.get("source") in {"windows_uia", "macos_ax"}:
                    store.db.execute("INSERT OR IGNORE INTO forward_reads VALUES (?,?,?,?,?,?,?)",
                                     (group, message["id"], "baseline", None, 0, None, None))
        return messages, []
    limit = config["capture"].get("forward_max_cards", 3)
    partial_retry = config["capture"].get("forward_partial_retry_seconds", 600)
    pause = store.db.execute("""SELECT MAX(next_attempt_at) FROM forward_reads
        WHERE group_name=? AND status='deferred' AND last_error IN
        ('user_postponed','user_active_during_notice','user_interrupted')""", (group,)).fetchone()[0]
    group_postponed = bool(pause and parse_time(pause) > now)
    result, diagnostics, opened, interrupted = [], [], 0, False
    for message in messages:
        if message.get("kind") != "forward" or message.get("children") or message.get("source") not in {"windows_uia", "macos_ax"}:
            result.append(message)
            continue
        cached = store.db.execute("SELECT * FROM forward_reads WHERE group_name=? AND parent_id=?",
                                  (group, message["id"])).fetchone()
        value = json.loads(cached["payload"]) if cached and cached["payload"] else message
        # Mac capture reads loaded AX rows, so it can never prove complete
        # history. Once it has useful body evidence, reuse it across restarts;
        # that provenance limit is not a reason to reopen an unchanged card.
        if message.get("source") == "macos_ax" and cached and value.get("children"):
            result.append(value)
            continue
        if cached and ((cached["status"] == "baseline" and not include_existing) or cached["status"] == "complete" or
                       (cached["next_attempt_at"] and parse_time(cached["next_attempt_at"]) > now)):
            result.append(value)
            continue
        if opened >= limit or interrupted or (message.get("source") == "macos_ax" and group_postponed):
            result.append(value)
            diagnostics.append({"parent_id": message["id"], "status": "deferred"})
            continue
        opened += 1
        attempts = cached["attempts"] if cached else 0
        try:
            if message.get("source") == "macos_ax":
                from .macos import expand_forward as mac_expand
                parent = mac_expand(config, message)
            else:
                parent = expand_forward(config, message)
            validate_message(parent, group)
        except (CaptureError, ValueError) as error:
            # Active input is expected before a Mac card can be opened. Wait
            # for the next idle poll without turning normal use into a long
            # exponential failure backoff. The native idle guard still runs.
            deferred = (message.get("source") == "macos_ax" and isinstance(error, CaptureError) and
                        str(error) in {"user_busy", "user_postponed", "user_active_during_notice", "user_interrupted"})
            status = "deferred" if deferred else "retry"
            if deferred:
                # Only the explicit Later button requests the long group-wide
                # pause. Normal activity retries after input becomes idle.
                delay = (config["capture"].get("forward_postpone_seconds", 300)
                         if str(error) == "user_postponed" else max(3, config["capture"].get("forward_idle_seconds", 3)))
            else:
                attempts += 1
                delay = min(900, 30 * 2 ** min(attempts - 1, 5))
            # Keep the last good partial evidence when a later re-read fails.
            with store.db:
                store.db.execute("""INSERT INTO forward_reads VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(group_name,parent_id) DO UPDATE SET
                    status=excluded.status,attempts=excluded.attempts,
                    next_attempt_at=excluded.next_attempt_at,last_error=excluded.last_error""",
                    (group, message["id"], status, None, attempts,
                     utc_text(now + timedelta(seconds=delay)), str(error)))
            result.append(value)
            diagnostics.append({"parent_id": message["id"], "status": status, "error": str(error)})
            # One conflicting UI action aborts the rest of this batch.
            interrupted = True
        else:
            complete = parent.get("source_meta", {}).get("history_complete") is True and not parent.get("incomplete")
            status = "complete" if complete else "partial"
            with store.db:
                store.db.execute("INSERT OR REPLACE INTO forward_reads VALUES (?,?,?,?,?,?,?)",
                    (group, message["id"], status, json.dumps(parent, ensure_ascii=False), 0,
                     None if complete or (message.get("source") == "macos_ax" and parent.get("children"))
                     else utc_text(now + timedelta(seconds=partial_retry)), None))
            result.append(parent)
            diagnostics.append({"parent_id": message["id"], "status": status,
                                "children": len(parent.get("children", []))})
    return result, diagnostics
