from __future__ import annotations

import unittest
from unittest.mock import Mock

from slackbot.rules.countdown_reaction import CountdownReactionRule


class CountdownReactionMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = Mock()
        self.slack = Mock()
        self.slack.get_permalink.return_value = "https://example.slack.com/message"
        self.rule = CountdownReactionRule(
            "contact_follow_up",
            {
                "source_channel": "C123",
                "source_bot_id": "B123",
                "source_username": "Join Request",
                "watch_duration_hours": 12,
                "warning_offsets_hours": [1],
            },
            self.storage,
            self.slack,
        )

    def test_accepts_matching_zapier_bot_message(self) -> None:
        self.rule.on_message(
            {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C123",
                "ts": "1234567890.123456",
                "bot_id": "B123",
                "username": "Join Request",
            }
        )

        self.slack.get_permalink.assert_called_once_with(
            "C123", "1234567890.123456"
        )
        self.storage.create_watch.assert_called_once()

    def test_still_accepts_matching_human_message(self) -> None:
        self.rule.options["source_user_id"] = "U123"

        self.rule.on_message(
            {
                "type": "message",
                "channel": "C123",
                "ts": "1234567890.123456",
                "user": "U123",
            }
        )

        self.storage.create_watch.assert_called_once()

    def test_rejects_message_edit(self) -> None:
        self.rule.on_message(
            {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C123",
                "ts": "1234567890.123456",
                "bot_id": "B123",
            }
        )

        self.storage.create_watch.assert_not_called()

    def test_rejects_thread_reply_without_subtype(self) -> None:
        self.rule.on_message(
            {
                "type": "message",
                "channel": "C123",
                "ts": "1234567891.123456",
                "thread_ts": "1234567890.123456",
                "bot_id": "B123",
            }
        )

        self.storage.create_watch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
