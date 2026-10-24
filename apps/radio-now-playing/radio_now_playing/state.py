from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .extract import identity_hash


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class LockError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, db_path):
        self.path = Path(db_path).parent / "daemon.lock"
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise LockError("Another daemon owns this state directory") from None
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()) + "\n")
        self.handle.flush()
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()


class State:
    def __init__(self, path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(path), timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS station_state (
                station TEXT PRIMARY KEY,
                last_seen_key TEXT,
                last_seen_text TEXT,
                sequence INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_delivered_at TEXT,
                last_delivered_key TEXT,
                last_error TEXT,
                consecutive_errors INTEGER NOT NULL DEFAULT 0,
                checks INTEGER NOT NULL DEFAULT 0,
                suppressed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                station TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                identity_key TEXT NOT NULL,
                display_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0,
                http_status INTEGER,
                reason TEXT,
                payload_hash TEXT,
                PRIMARY KEY(station, sequence),
                FOREIGN KEY(station) REFERENCES station_state(station)
            );
            PRAGMA user_version=1;
        """)
        # A crash after starting a POST has an ambiguous outcome, so do not replay.
        with self.conn:
            self.conn.execute("UPDATE deliveries SET status='uncertain', reason='interrupted_delivery', completed_at=? WHERE status='pending'", (now_iso(),))

    def close(self):
        self.conn.close()

    def observe(self, station, play):
        timestamp = now_iso()
        with self.conn:
            previous = self.conn.execute("SELECT * FROM station_state WHERE station=?", (station.id,)).fetchone()
            first = previous is None or previous["last_seen_key"] is None
            sequence = (previous["sequence"] if previous else 0)
            changed = first or previous["last_seen_key"] != play.identity_key
            if changed and not first:
                sequence += 1
            self.conn.execute("""
                INSERT INTO station_state(station,last_seen_key,last_seen_text,sequence,last_checked_at,last_success_at,checks)
                VALUES(?,?,?,?,?,?,1)
                ON CONFLICT(station) DO UPDATE SET
                  last_seen_key=excluded.last_seen_key,last_seen_text=excluded.last_seen_text,
                  sequence=excluded.sequence,last_checked_at=excluded.last_checked_at,
                  last_success_at=excluded.last_success_at,last_error=NULL,consecutive_errors=0,
                  checks=station_state.checks+1
            """, (station.id, play.identity_key, play.display_text, sequence, timestamp, timestamp))
            if changed and (not first or station.initial_post):
                self.conn.execute("""INSERT INTO deliveries(station,sequence,identity_key,display_text,status,created_at)
                                     VALUES(?,?,?,?, 'ready',?)""", (station.id, sequence, play.identity_key, play.display_text, timestamp))
            event = self.conn.execute("SELECT * FROM deliveries WHERE station=? AND sequence=?", (station.id, sequence)).fetchone()
            if event is None or event["status"] in ("sent", "uncertain", "pending", "rejected"):
                self.conn.execute("UPDATE station_state SET suppressed=suppressed+1 WHERE station=?", (station.id,))
                return None, "baseline" if first and not station.initial_post else "unchanged"
            if event["retry_at"] > time.time():
                return None, "delivery_backoff"
            return dict(event), "changed" if changed else "delivery_retry"

    def begin_delivery(self, station, sequence, payload_hash):
        with self.conn:
            cursor = self.conn.execute("""UPDATE deliveries SET status='pending',attempts=attempts+1,payload_hash=?
                                        WHERE station=? AND sequence=? AND status IN ('ready','retryable') AND attempts<3""",
                                       (payload_hash, station, sequence))
            return cursor.rowcount == 1

    def finish_delivery(self, station, sequence, result):
        status = result.status
        timestamp = now_iso()
        with self.conn:
            event = self.conn.execute("SELECT * FROM deliveries WHERE station=? AND sequence=?", (station, sequence)).fetchone()
            if status == "retryable" and event["attempts"] >= 3:
                status = "rejected"
            self.conn.execute("""UPDATE deliveries SET status=?,completed_at=?,retry_at=?,http_status=?,reason=?
                                 WHERE station=? AND sequence=?""",
                              (status, timestamp, time.time() + (result.retry_after or 0), result.http_status, result.reason, station, sequence))
            if status == "sent":
                self.conn.execute("UPDATE station_state SET last_delivered_at=?,last_delivered_key=? WHERE station=?",
                                  (timestamp, event["identity_key"], station))
        return status

    def failed_fetch(self, station, reason):
        with self.conn:
            self.conn.execute("""INSERT INTO station_state(station,last_checked_at,last_error,consecutive_errors,checks)
                                 VALUES(?,?,?,1,1) ON CONFLICT(station) DO UPDATE SET
                                 last_checked_at=excluded.last_checked_at,last_error=excluded.last_error,
                                 consecutive_errors=station_state.consecutive_errors+1,checks=station_state.checks+1""",
                              (station, now_iso(), reason))

    def import_identity(self, station, identity, text, checked_at=None, posted_at=None):
        with self.conn:
            cursor = self.conn.execute("""INSERT OR IGNORE INTO station_state
                (station,last_seen_key,last_seen_text,last_checked_at,last_success_at,last_delivered_at,last_delivered_key)
                VALUES(?,?,?,?,?,?,?)""",
                (station, identity_hash(identity), text, checked_at, checked_at, posted_at, identity_hash(identity) if posted_at else None))
            return cursor.rowcount == 1

    def report(self):
        return {
            "stations": [dict(row) for row in self.conn.execute("SELECT * FROM station_state ORDER BY station")],
            "delivery_counts": [dict(row) for row in self.conn.execute("SELECT station,status,count(*) AS count FROM deliveries GROUP BY station,status ORDER BY station,status")],
            "recent_deliveries": [dict(row) for row in self.conn.execute("SELECT * FROM deliveries ORDER BY created_at DESC LIMIT 20")],
        }


def readonly_report(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        return {
            "stations": [dict(row) for row in conn.execute("SELECT * FROM station_state ORDER BY station")],
            "delivery_counts": [dict(row) for row in conn.execute("SELECT station,status,count(*) AS count FROM deliveries GROUP BY station,status ORDER BY station,status")],
            "recent_deliveries": [dict(row) for row in conn.execute("SELECT * FROM deliveries ORDER BY created_at DESC LIMIT 20")],
        }
    finally:
        conn.close()
