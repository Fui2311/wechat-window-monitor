"""SQLite ownership: independent registry, immutable rule versions and capture evidence."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from wechat_tracker.domain.messages import stable_message, validate_message


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Repository:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "monitor.sqlite3"
        with self.connect() as db:
            db.executescript("""
CREATE TABLE IF NOT EXISTS windows(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, config TEXT NOT NULL,
 active_rule TEXT, initialized INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES windows(id),
 version INTEGER NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL, origin TEXT NOT NULL,
 UNIQUE(window_id, version));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT NOT NULL REFERENCES windows(id),
 fingerprint TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL, state TEXT NOT NULL,
 rule_id TEXT, result TEXT, error TEXT NOT NULL DEFAULT '', UNIQUE(window_id, fingerprint));
CREATE INDEX IF NOT EXISTS messages_window ON messages(window_id, id DESC);
""")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_windows(self):
        with self.connect() as db:
            return [
                dict(
                    json.loads(r["config"]),
                    id=r["id"],
                    name=r["name"],
                    active_rule=r["active_rule"],
                    revision=r["revision"],
                    initialized=bool(r["initialized"]),
                )
                for r in db.execute("SELECT * FROM windows ORDER BY created_at, id")
            ]

    def window(self, identity):
        return next((w for w in self.list_windows() if w["id"] == identity), None)

    def save_window(self, config, identity=None, expected_revision=None):
        with self.connect() as db:
            if identity:
                old = db.execute(
                    "SELECT * FROM windows WHERE id=?", (identity,)
                ).fetchone()
                if old is None:
                    raise ValueError("窗口不存在")
                if expected_revision != old["revision"]:
                    raise ValueError("配置已在另一页面更新，请刷新后再保存")
                old_cfg = json.loads(old["config"])
                invalidate = any(old_cfg[k] != config[k] for k in ("target", "mode"))
                rebind = (
                    old["name"] != config["name"]
                    or old_cfg["capture"] != config["capture"]
                )
                db.execute(
                    "UPDATE windows SET name=?, config=?, revision=revision+1, active_rule=?, initialized=? WHERE id=?",
                    (
                        config["name"],
                        encode(config),
                        None if invalidate else old["active_rule"],
                        0 if rebind else old["initialized"],
                        identity,
                    ),
                )
            else:
                if db.execute("SELECT COUNT(*) FROM windows").fetchone()[0] >= 20:
                    raise ValueError("最多配置20个窗口")
                identity = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO windows(id,name,config,created_at) VALUES (?,?,?,?)",
                    (identity, config["name"], encode(config), now()),
                )
        return self.window(identity)

    def save_rule(self, identity, rule, origin="llm"):
        with self.connect() as db:
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM rules WHERE window_id=?",
                (identity,),
            ).fetchone()[0]
            key = uuid.uuid4().hex
            db.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?,?)",
                (key, identity, version, encode(rule), now(), origin),
            )
        return self.rule(identity, key)

    def rules(self, identity):
        with self.connect() as db:
            return [
                dict(r, spec=json.loads(r["spec"]))
                for r in db.execute(
                    "SELECT * FROM rules WHERE window_id=? ORDER BY version DESC",
                    (identity,),
                )
            ]

    def rule(self, identity, rule_id):
        return next((r for r in self.rules(identity) if r["id"] == rule_id), None)

    def activate(self, identity, rule_id):
        with self.connect() as db:
            if not db.execute(
                "SELECT 1 FROM rules WHERE id=? AND window_id=?", (rule_id, identity)
            ).fetchone():
                raise ValueError("规则版本不属于该窗口")
            db.execute(
                "UPDATE windows SET active_rule=?, revision=revision+1 WHERE id=?",
                (rule_id, identity),
            )

    def ingest(self, window, messages):
        if not isinstance(messages, list) or len(messages) > 2000:
            raise ValueError("采样帧必须为最多2000条消息的列表")
        for message in messages:
            validate_message(message, window["name"])
        with self.connect() as db:
            initialized = db.execute(
                "SELECT initialized FROM windows WHERE id=?", (window["id"],)
            ).fetchone()[0]
            for message in messages:
                fingerprint = hashlib.sha256(
                    encode(stable_message(message)).encode()
                ).hexdigest()
                state = (
                    "pending"
                    if initialized or window["include_existing"]
                    else "baseline"
                )
                db.execute(
                    "INSERT OR IGNORE INTO messages(window_id,fingerprint,payload,received_at,state) VALUES (?,?,?,?,?)",
                    (window["id"], fingerprint, encode(message), now(), state),
                )
            db.execute("UPDATE windows SET initialized=1 WHERE id=?", (window["id"],))

    def pending(self, identity, limit=50):
        with self.connect() as db:
            return [
                dict(r, payload=json.loads(r["payload"]))
                for r in db.execute(
                    "SELECT * FROM messages WHERE window_id=? AND state='pending' ORDER BY id LIMIT ?",
                    (identity, limit),
                )
            ]

    def finish(self, identity, message_id, rule_id, result=None, error=""):
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET state=?, rule_id=?, result=?, error=? WHERE window_id=? AND id=?",
                (
                    "error" if error else "processed",
                    rule_id,
                    encode(result) if result is not None else None,
                    error,
                    identity,
                    message_id,
                ),
            )

    def retry(self, identity):
        with self.connect() as db:
            db.execute(
                "UPDATE messages SET state='pending',error='' WHERE window_id=? AND state='error'",
                (identity,),
            )
            return db.total_changes

    def records(self, identity, *, before=None, limit=50, search="", category="all"):
        where = "window_id=?"
        params = [identity]
        if before is not None:
            where += " AND id<?"
            params.append(before)
        if search:
            where += " AND instr(lower(payload),lower(?))>0"
            params.append(search)
        if category == "matched":
            where += " AND state='processed' AND result IS NOT NULL AND json_extract(result,'$.matched')=1"
        elif category == "error":
            where += " AND state='error'"
        with self.connect() as db:
            rows = list(
                db.execute(
                    f"SELECT * FROM messages WHERE {where} ORDER BY id DESC LIMIT ?",
                    (*params, limit + 1),
                )
            )
            records = [
                dict(
                    r,
                    payload=json.loads(r["payload"]),
                    result=json.loads(r["result"]) if r["result"] else None,
                )
                for r in rows[:limit]
            ]
        return dict(
            records=records,
            has_more=len(rows) > limit,
            next_before=records[-1]["id"] if records else None,
        )

    def counts(self, identity):
        with self.connect() as db:
            return dict(
                db.execute(
                    "SELECT state,COUNT(*) FROM messages WHERE window_id=? GROUP BY state",
                    (identity,),
                ).fetchall()
            ) | {
                "matched": db.execute(
                    "SELECT COUNT(*) FROM messages WHERE window_id=? AND json_extract(result,'$.matched')=1",
                    (identity,),
                ).fetchone()[0]
            }
