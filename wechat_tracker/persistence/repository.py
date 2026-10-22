"""SQLite repository. All write transaction boundaries belong to use cases.

Store owns connection setup, schema and backup. This adapter owns queries and
atomic outbox claims. Neither knows about extraction, UI automation or SMTP.
"""
import json

from ..store import Store


class SQLiteRepository:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    def transaction(self):
        return self.db

    def message_exists(self, key):
        return bool(self.db.execute("SELECT 1 FROM messages WHERE key=?", (key,)).fetchone())

    def add_message(self, key, message, received_at):
        return bool(self.db.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?)",
                                   (key, json.dumps(message, ensure_ascii=False), received_at)).rowcount)

    def messages(self):
        return [dict(payload=json.loads(row[0]), received_at=row[1]) for row in self.db.execute(
            "SELECT payload,received_at FROM messages ORDER BY received_at,rowid")]

    def events(self):
        return self.store.events()

    def event_exists(self, event_id):
        return bool(self.db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone())

    def matching_events(self, title, group):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM events WHERE title_key=? AND group_name=?", (title, group))]

    def save_event(self, event, title, group):
        self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?)",
                        (event["id"], title, group, json.dumps(event, ensure_ascii=False)))

    def has_delivered_jobs(self):
        return bool(self.db.execute("SELECT 1 FROM outbox WHERE status IN ('sent','sending') LIMIT 1").fetchone())

    def pending_payloads(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM outbox WHERE status='pending'")]

    def version_floors(self):
        return dict(self.db.execute("SELECT event_id,MAX(version) FROM outbox GROUP BY event_id").fetchall())

    def begin_rebuild(self):
        self.db.execute("UPDATE outbox SET status='superseded',claimed_at=NULL WHERE status='pending'")
        self.db.execute("DELETE FROM events")

    def expire_reminders(self, now):
        self.db.execute("UPDATE outbox SET status='expired' WHERE status='pending' AND reason='reminder' AND due_at<=?", (now,))

    def pending_count(self):
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]

    def enqueue(self, event, reason, due_at, suffix=""):
        self.db.execute("""INSERT OR IGNORE INTO outbox
            (dedup_key,event_id,version,reason,due_at,payload) VALUES (?,?,?,?,?,?)""",
            (f"{event['id']}:{event['version']}:{reason}:{suffix}", event["id"], event["version"],
             reason, due_at, json.dumps(event, ensure_ascii=False)))

    def supersede_event_jobs(self, event_id):
        self.db.execute("UPDATE outbox SET status='superseded' WHERE event_id=? AND status IN ('pending','sending')", (event_id,))

    def maintain_jobs(self, stale_before, reminders_enabled):
        if not reminders_enabled:
            self.db.execute("""UPDATE outbox SET status='superseded',claimed_at=NULL
                WHERE reason='reminder' AND status IN ('pending','sending')""")
        self.db.execute("""UPDATE outbox SET status='pending',claimed_at=NULL
            WHERE status='sending' AND claimed_at < ?""", (stale_before,))
        self.db.execute("""UPDATE outbox SET status='superseded',claimed_at=NULL
            WHERE status IN ('pending','sending') AND EXISTS (
                SELECT 1 FROM events WHERE events.id=outbox.event_id
                AND (CAST(json_extract(events.payload,'$.version') AS INTEGER) != outbox.version
                OR (json_extract(events.payload,'$.status')='cancelled' AND outbox.reason IN ('new','update','reminder')))
            )""")

    def due_jobs(self, now):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM outbox WHERE status='pending' AND due_at<=? ORDER BY due_at,id LIMIT 100", (now,))]

    def claim_job(self, job_id, now):
        return bool(self.db.execute("""UPDATE outbox SET status='sending',claimed_at=?
            WHERE id=? AND status='pending' AND EXISTS (
                SELECT 1 FROM events WHERE events.id=outbox.event_id
                AND CAST(json_extract(events.payload,'$.version') AS INTEGER)=outbox.version
            )""", (now, job_id)).rowcount)

    def finish_job(self, job_id, status):
        expected = "sending" if status == "sent" else "pending"
        self.db.execute("UPDATE outbox SET status=?,claimed_at=NULL WHERE id=? AND status=?", (status, job_id, expected))

    def fail_job(self, job_id, status, attempts, error, due_at):
        self.db.execute("""UPDATE outbox SET status=?,attempts=?,last_error=?,due_at=?,claimed_at=NULL
            WHERE id=? AND status='sending'""", (status, attempts, error, due_at, job_id))

    def capture_initialized(self, group):
        return bool(self.db.execute("SELECT 1 FROM capture_state WHERE group_name=?", (group,)).fetchone())

    def capture_seen(self, group, fingerprint):
        return bool(self.db.execute("SELECT 1 FROM capture_seen WHERE group_name=? AND fingerprint=?", (group, fingerprint)).fetchone())

    def mark_seen(self, group, fingerprint):
        self.db.execute("INSERT OR IGNORE INTO capture_seen VALUES (?,?)", (group, fingerprint))

    def mark_capture(self, group, now):
        self.db.execute("INSERT OR REPLACE INTO capture_state VALUES (?,?)", (group, now))
