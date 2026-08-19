"""
Lightweight SQLite persistence layer.

Everything the bot needs to survive a restart -- active watches and their
pending notifications -- lives here. No Slack-specific logic belongs in
this file; it's pure bookkeeping.

Design: rather than scheduling in-memory timers (which vanish on
restart), each "watch" gets one or more "notifications" rows with a
due_at timestamp. A poller (see scheduler.py) periodically asks this
storage what's due and hands it off. This means the DB is always the
single source of truth for what still needs to happen.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    message_ts TEXT NOT NULL,
    permalink TEXT,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active | cancelled | expired
    cancelled_by TEXT,
    cancelled_at REAL,
    UNIQUE(source_channel, message_ts)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    kind TEXT NOT NULL,          -- e.g. 'warning', 'expired' -- rule-defined
    due_at REAL NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    sent_at REAL
);

CREATE INDEX IF NOT EXISTS idx_notifications_due
    ON notifications (sent, due_at);
"""


@dataclass
class Watch:
    id: int
    rule_name: str
    source_channel: str
    message_ts: str
    permalink: Optional[str]
    created_at: float
    status: str
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[float] = None


@dataclass
class Notification:
    id: int
    watch_id: int
    kind: str
    due_at: float
    sent: bool


class Storage:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        finally:
            cur.close()

    def create_watch(
        self,
        rule_name: str,
        source_channel: str,
        message_ts: str,
        notification_offsets: List[Tuple[str, float]],  # [(kind, due_at_epoch), ...]
        permalink: Optional[str] = None,
    ) -> Optional[Watch]:
        """
        Creates a watch plus its scheduled notifications. Returns None (and
        does nothing) if a watch for this exact message already exists --
        this makes handling is idempotent against duplicate/retried Slack
        events, which happen in practice.
        """
        now = time.time()
        with self._cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO watches (rule_name, source_channel, message_ts, "
                    "permalink, created_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
                    (rule_name, source_channel, message_ts, permalink, now),
                )
            except sqlite3.IntegrityError:
                return None  # already being watched

            watch_id = cur.lastrowid
            for kind, due_at in notification_offsets:
                cur.execute(
                    "INSERT INTO notifications (watch_id, kind, due_at) VALUES (?, ?, ?)",
                    (watch_id, kind, due_at),
                )

        return Watch(watch_id, rule_name, source_channel, message_ts, permalink, now, "active")

    def find_active_watch(self, channel: str, message_ts: str) -> Optional[Watch]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM watches WHERE source_channel=? AND message_ts=? AND status='active'",
                (channel, message_ts),
            )
            row = cur.fetchone()
            return Watch(**dict(row)) if row else None

    def cancel_watch(self, watch_id: int, cancelled_by: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE watches SET status='cancelled', cancelled_by=?, cancelled_at=? WHERE id=?",
                (cancelled_by, time.time(), watch_id),
            )

    def due_notifications(self) -> List[Tuple[Notification, Watch]]:
        """
        Returns (Notification, Watch) pairs that are due, unsent, and
        belong to a watch that's still active (i.e. not cancelled).
        """
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT n.id as n_id, n.watch_id as n_watch_id, n.kind as n_kind,
                       n.due_at as n_due_at, n.sent as n_sent,
                       w.rule_name, w.source_channel, w.message_ts, w.permalink,
                       w.created_at as w_created_at, w.status as w_status
                FROM notifications n
                JOIN watches w ON w.id = n.watch_id
                WHERE n.sent = 0 AND n.due_at <= ? AND w.status = 'active'
                ORDER BY n.due_at ASC
                """,
                (now,),
            )
            rows = cur.fetchall()

        results = []
        for row in rows:
            d = dict(row)
            notification = Notification(d["n_id"], d["n_watch_id"], d["n_kind"], d["n_due_at"], bool(d["n_sent"]))
            watch = Watch(
                d["n_watch_id"], d["rule_name"], d["source_channel"], d["message_ts"],
                d["permalink"], d["w_created_at"], d["w_status"],
            )
            results.append((notification, watch))
        return results

    def mark_sent(self, notification_id: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE notifications SET sent=1, sent_at=? WHERE id=?",
                (time.time(), notification_id),
            )
