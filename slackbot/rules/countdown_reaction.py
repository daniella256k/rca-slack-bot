"""
CountdownReactionRule
======================
Generic "watch a message, and if it isn't acknowledged within a time
window, escalate" rule. This is what powers the join-request follow-up
use case, but nothing about it is hardcoded to that -- it's entirely
config-driven, so it can be reused for other similar SLA-style workflows
just by adding another `type: countdown_reaction` block to config.yaml.

Relevant options (set per-rule under `rules:` in config.yaml):

  source_channel: "C0123456"        # channel to watch for new messages
  source_bot_id: "B0123456"         # match messages from this app/bot...
  source_user_id: null              # ...or from this human user (either/or)
  source_username: null             # ...or this incoming-webhook display name

  watch_duration_hours: 12          # total time allotted
  warning_offsets_hours: [1]        # send a warning this many hours *before*
                                     # the deadline (list supports multiple
                                     # staggered warnings, e.g. [4, 1])

  cancel_emojis: ["white_check_mark", "x"]   # reacting with any of these
                                              # to the original message
                                              # marks it as handled

  target_channel: "C0654321"        # where warning messages get posted
  mention: "<!channel>"             # Slack mention syntax
  warning_template: "..."           # see config.example.yaml for the
                                     # available {placeholders}
"""
from __future__ import annotations

import time

from .base import Rule, register_rule
from ..storage import Notification, Watch


@register_rule("countdown_reaction")
class CountdownReactionRule(Rule):
    def _matches_source(self, event: dict) -> bool:
        if event.get("channel") != self.options.get("source_channel"):
            return False

        bot_id = self.options.get("source_bot_id")
        user_id = self.options.get("source_user_id")
        username = self.options.get("source_username")

        # App/bot posts carry bot_id (and typically no 'user'), posts from a
        # real person carry 'user', and incoming webhooks can carry 'username'.
        # Match case-insensitively on any configured source.
        configured_sources = (
            (bot_id, event.get("bot_id")),
            (user_id, event.get("user")),
            (username, event.get("username")),
        )
        return any(
            bool(configured)
            and isinstance(configured, str)
            and isinstance(actual, str)
            and configured.casefold() == actual.casefold()
            for configured, actual in configured_sources
        )

    def on_message(self, event: dict) -> None:
        # Accept normal human posts and app/webhook posts (`bot_message`).
        # Skip edits, deletes, thread broadcasts, channel_join notices, etc.
        if event.get("subtype") not in (None, "bot_message"):
            return
        # A thread reply commonly has no subtype, so check thread_ts
        # separately to ensure only new top-level messages start watches.
        if event.get("thread_ts") is not None:
            return
        if not self._matches_source(event):
            return

        channel = event["channel"]
        message_ts = event["ts"]
        created_at = time.time()

        duration_hours = float(self.options.get("watch_duration_hours", 12))
        warning_offsets = self.options.get("warning_offsets_hours", [1])

        deadline_at = created_at + duration_hours * 3600
        notification_offsets = [
            ("warning", deadline_at - float(offset) * 3600) for offset in warning_offsets
        ]

        permalink = self.slack.get_permalink(channel, message_ts)

        watch = self.storage.create_watch(
            rule_name=self.name,
            source_channel=channel,
            message_ts=message_ts,
            notification_offsets=notification_offsets,
            permalink=permalink,
        )
        if watch is None:
            return  # duplicate event for a message we're already watching

    def on_reaction(self, event: dict) -> None:
        if event.get("reaction") not in self.options.get("cancel_emojis", []):
            return

        item = event.get("item", {})
        if item.get("type") != "message":
            return
        if item.get("channel") != self.options.get("source_channel"):
            return

        watch = self.storage.find_active_watch(item.get("channel"), item.get("ts"))
        if watch and watch.rule_name == self.name:
            self.storage.cancel_watch(watch.id, cancelled_by=event.get("user", "unknown"))

    def on_notification(self, notification: Notification, watch: Watch) -> None:
        if notification.kind != "warning":
            return  # room for future kinds, e.g. 'expired', with no code changes here

        target_channel = self.options["target_channel"]
        mention = self.options.get("mention", "<!channel>")
        age_hours = round((time.time() - watch.created_at) / 3600, 1)

        template = self.options.get(
            "warning_template",
            "{mention} A watched item is approaching its deadline "
            "(posted {age_hours}h ago). {permalink}",
        )
        text = template.format(
            mention=mention,
            age_hours=age_hours,
            permalink=watch.permalink or "",
        )
        self.slack.post_message(target_channel, text)
