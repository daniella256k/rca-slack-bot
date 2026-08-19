"""
Polling scheduler: periodically checks storage for due notifications and
hands each one to the rule that owns its watch.

Deliberately polls a SQLite table instead of using in-memory timers, so
the bot survives restarts without losing track of anything -- the DB
(storage.py) is always the single source of truth for what's still due.
"""
from __future__ import annotations

import logging
from typing import Dict

from apscheduler.schedulers.background import BackgroundScheduler

from .rules.base import Rule
from .storage import Storage

logger = logging.getLogger(__name__)


class NotificationScheduler:
    def __init__(self, storage: Storage, rules: Dict[str, Rule], poll_interval_seconds: int):
        self.storage = storage
        self.rules = rules
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._tick, "interval", seconds=poll_interval_seconds, id="due_notifications_tick"
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info("Notification scheduler started.")

    def shutdown(self) -> None:
        self._scheduler.shutdown()

    def _tick(self) -> None:
        for notification, watch in self.storage.due_notifications():
            rule = self.rules.get(watch.rule_name)
            if rule is None:
                logger.warning(
                    "No loaded rule named '%s' for a pending notification; skipping.",
                    watch.rule_name,
                )
                continue
            try:
                rule.on_notification(notification, watch)
            except Exception:
                logger.exception(
                    "Rule '%s' failed handling notification %s -- will retry next tick.",
                    watch.rule_name, notification.id,
                )
                continue  # leave unsent so we retry
            self.storage.mark_sent(notification.id)
