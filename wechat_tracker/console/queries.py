"""Read-only SQLite views for captured evidence and database selection."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from ..paths import PROJECT_ROOT
from .presentation import _delivery_info
from .discovery_policy import database_candidates


def delivery_backlog(db_path: Path, now: str) -> dict:
    """Conservative pending counts for explicit send-mode disclosure, without writes."""
    result = dict(pending=0, due=0, retrying=0, failed=0, oldest_due_at=None)
    current = datetime.fromisoformat(now)
    oldest = None
    if not db_path.is_file():
        return result
    with _safe_db_connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone():
            return result
        for row in conn.execute("SELECT status, due_at, attempts FROM outbox WHERE status IN ('pending', 'dead')"):
            if row['status'] == 'dead':
                result['failed'] += 1
                continue
            result['pending'] += 1
            result['retrying'] += bool(row['attempts'])
            # All persisted due times are UTC ISO values; parse offsets for legacy rows.
            try:
                due = datetime.fromisoformat(row['due_at'].replace('Z', '+00:00'))
                result['due'] += due <= current
                if oldest is None or due < oldest:
                    oldest = due
                    result['oldest_due_at'] = row['due_at']
            except (ValueError, TypeError, AttributeError):
                pass
    return result

@contextmanager
def _safe_db_connect(db_path: Path | str):
    target = Path(db_path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Database not found: {target}")
    conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _captured_messages(db_path: Path, *, category="all", search="", limit=100, offset=0) -> dict:
    """Read stored capture evidence without reanalysis or database writes."""
    counts = {"all": 0, "non_lecture": 0, "lecture": 0}
    result = {"messages": [], "counts": counts, "total": 0, "offset": offset,
              "limit": limit, "has_more": False, "latest_received_at": None,
              "last_success_at": None, "unreadable_count": 0}
    if not db_path.is_file():
        return result

    with _safe_db_connect(db_path) as conn:
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources = set()
        if "events" in tables:
            for row in conn.execute("SELECT group_name, payload FROM events"):
                try:
                    event = json.loads(row["payload"])
                    sources.add((event.get("source_group") or row["group_name"],
                                 event.get("source_message_id") or "", event.get("source_text") or ""))
                except (ValueError, TypeError, AttributeError):
                    continue
        if "capture_state" in tables:
            row = conn.execute("SELECT MAX(last_success_at) FROM capture_state").fetchone()
            result["last_success_at"] = row[0]
        if "messages" not in tables:
            return result

        def related(node, group, inherited_id="", depth=0):
            if not isinstance(node, dict) or depth > 6:
                return False
            identity = node.get("id") or inherited_id
            text = node.get("text") or ""
            if (group, identity, text) in sources or (group, "", text) in sources:
                return True
            return any(related(child, group, f"{identity or 'message'}/child-{index + 1}", depth + 1)
                       for index, child in enumerate(node.get("children") or []))

        def content(node, depth=0):
            # Never expose source_meta, transport internals or arbitrary fields.
            fields = ("id", "group", "sender", "kind", "text", "sent_at", "name")
            item = {key: node[key] for key in fields if isinstance(node.get(key), str)}
            if depth < 6:
                for key in ("children", "attachments"):
                    item[key] = [content(child, depth + 1) for child in (node.get(key) or [])
                                 if isinstance(child, dict)]
            return item

        def matches_search(item):
            return (any(search.casefold() in item.get(key, "").casefold() for key in ("sender", "text", "name"))
                    or any(matches_search(child) for key in ("children", "attachments") for child in item.get(key, [])))

        query = "SELECT key, payload, received_at FROM messages ORDER BY received_at DESC, rowid DESC"
        for row in conn.execute(query):
            if result["latest_received_at"] is None:
                result["latest_received_at"] = row["received_at"]
            try:
                message = json.loads(row["payload"])
                if not isinstance(message, dict):
                    raise ValueError("Invalid stored message")
                item_category = "lecture" if related(message, message.get("group")) else "non_lecture"
                item = content(message)
            except (ValueError, TypeError, AttributeError):
                result["unreadable_count"] += 1
                continue
            counts["all"] += 1
            counts[item_category] += 1
            if category != "all" and category != item_category:
                continue
            if search and not matches_search(item):
                continue
            result["total"] += 1
            if offset < result["total"] <= offset + limit:
                result["messages"].append(dict(item, key=row["key"], received_at=row["received_at"],
                                               category=item_category))
        result["has_more"] = result["total"] > offset + limit
    return result


def _list_databases(active_data_dir: str, current_group: str, services=()) -> list[dict]:
    results = []
    seen = set()

    # 1. Active database
    active_db = Path(active_data_dir).resolve() / "tracker.sqlite3"
    active_count = 0
    if active_db.is_file():
        try:
            with _safe_db_connect(active_db) as conn:
                active_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except Exception:
            pass
    results.append({
        "path": str(active_db),
        "label": f"当前活动库 ({current_group})",
        "is_active": True,
        "is_demo": False,
        "group_name": current_group,
        "event_count": active_count,
    })
    seen.add(str(active_db))

    # 2. Search common state directories, explicitly excluding backup directories
    search_roots = [Path(active_data_dir).resolve().parent, PROJECT_ROOT / "state"]
    search_roots.extend(Path(svc["data_dir"]).resolve() for svc in services if svc.get("data_dir"))
    service_by_db = {str(Path(svc["data_dir"]).resolve() / "tracker.sqlite3"): svc for svc in services if svc.get("data_dir")}
    for root in dict.fromkeys(search_roots):
        if not root.is_dir():
            continue
        for sqlite_path in database_candidates(root):
            resolved = str(sqlite_path.resolve())
            if resolved in seen:
                continue

            name_lower = sqlite_path.name.lower()
            parent_lower = sqlite_path.parent.name.lower()
            seen.add(resolved)
            event_count = 0
            db_group = None
            is_demo = "demo" in name_lower or "demo" in parent_lower
            try:
                with _safe_db_connect(sqlite_path) as conn:
                    table_exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
                    ).fetchone()
                    if table_exists:
                        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                        row = conn.execute("SELECT group_name FROM events LIMIT 1").fetchone()
                        if row and row[0]:
                            db_group = str(row[0])
                    if not db_group:
                        tbl_cs = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_state'"
                        ).fetchone()
                        if tbl_cs:
                            row_cs = conn.execute("SELECT group_name FROM capture_state LIMIT 1").fetchone()
                            if row_cs and row_cs[0]:
                                db_group = str(row_cs[0])
            except Exception:
                continue

            if is_demo:
                label = f"Demo 演示库 ({sqlite_path.parent.name}/{sqlite_path.name})"
            elif db_group:
                label = f"历史只读库 - {db_group} ({sqlite_path.parent.name})"
            else:
                if not event_count:
                    continue
                label = f"历史只读库 ({sqlite_path.parent.name}/{sqlite_path.name})"

            live_service = service_by_db.get(resolved, {})
            if live_service.get("running"):
                label = f"运行中服务库 - {db_group or live_service.get('group', '')}"

            results.append({
                "path": resolved,
                "label": label,
                "is_active": False,
                "is_demo": is_demo,
                "group_name": db_group or "",
                "event_count": event_count,
                "is_live_service": bool(live_service.get("running")),
            })

    return results




def read_events(db_path: Path, service: dict) -> list[dict]:
    events = []
    with _safe_db_connect(db_path) as conn:
        table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        if table_exists:
            for row in conn.execute("SELECT payload FROM events ORDER BY rowid DESC"):
                try:
                    events.append(json.loads(row[0]))
                except Exception:
                    pass
        jobs_by_event = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone():
            for job in conn.execute("SELECT * FROM outbox WHERE reason!='reminder' ORDER BY id DESC"):
                jobs_by_event.setdefault(job["event_id"], dict(job))
        for event in events:
            job = jobs_by_event.get(event.get("id"))
            event["delivery"] = (_delivery_info(job, service) if job else
                                 {"state": "no_job", "label": "未生成邮件任务", "message": "尚无对应邮件队列记录。"})
    return events


def read_jobs(db_path: Path, service: dict) -> list[dict]:
    jobs = []
    with _safe_db_connect(db_path) as conn:
        table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone()
        if table_exists:
            for row in conn.execute("SELECT * FROM outbox ORDER BY due_at DESC, id DESC"):
                item = dict(row)
                try:
                    item["payload"] = json.loads(item["payload"])
                except Exception:
                    pass
                item["delivery"] = _delivery_info(item, service)
                jobs.append(item)
    return jobs


def read_preview_target(db_path: Path, event_id=None, job_id=None) -> tuple[dict | None, str]:
    target_event = None
    target_reason = "new"

    with _safe_db_connect(db_path) as conn:
        if job_id:
            job_id_val = int(job_id) if job_id and str(job_id).isdigit() else job_id
            row = conn.execute("SELECT reason, payload FROM outbox WHERE id = ?", (job_id_val,)).fetchone()
            if row:
                target_reason = row["reason"]
                target_event = json.loads(row["payload"])
        elif event_id:
            row = conn.execute("SELECT payload FROM events WHERE id = ?", (event_id,)).fetchone()
            if row:
                target_event = json.loads(row["payload"])
                target_reason = "cancelled" if target_event.get("status") == "cancelled" else (
                    "review" if target_event.get("needs_review") else "new"
                )

    return target_event, target_reason


def calendar_events(db_path: Path) -> list[dict]:
    events = []
    if db_path.is_file():
        with _safe_db_connect(db_path) as conn:
            table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
            if table_exists:
                for row in conn.execute("SELECT payload FROM events ORDER BY rowid"):
                    try:
                        events.append(json.loads(row[0]))
                    except Exception:
                        pass

    return events
