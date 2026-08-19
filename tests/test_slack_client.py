from __future__ import annotations

import unittest
from unittest.mock import Mock, call

from slackbot.slack_client import SlackClient, parse_message_permalink


class FakeSlackResponse:
    """Minimal SlackResponse shape; intentionally not directly iterable."""

    def __init__(self, data: dict):
        self.data = data


class ParseMessagePermalinkTests(unittest.TestCase):
    def test_parses_top_level_message(self) -> None:
        parsed = parse_message_permalink(
            "https://chicagoimt.slack.com/archives/C0123456/p1234567890123456"
        )

        self.assertEqual(parsed.channel, "C0123456")
        self.assertEqual(parsed.message_ts, "1234567890.123456")
        self.assertIsNone(parsed.thread_ts)

    def test_parses_thread_reply(self) -> None:
        parsed = parse_message_permalink(
            "https://example.slack.com/archives/C0123456/p1234567890654321"
            "?thread_ts=1234567890.111111&cid=C0123456"
        )

        self.assertEqual(parsed.message_ts, "1234567890.654321")
        self.assertEqual(parsed.thread_ts, "1234567890.111111")

    def test_accepts_epoch_seconds_only(self) -> None:
        parsed = parse_message_permalink(
            "https://example.slack.com/archives/C0123456/p1234567890"
        )

        self.assertEqual(parsed.message_ts, "1234567890.000000")

    def test_rejects_non_slack_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "slack.com"):
            parse_message_permalink("https://example.com/archives/C0123456/p1234567890")


class SlackClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = SlackClient("xoxb-test")
        self.client.web = Mock()

    def test_channel_id_passes_through_without_api_lookup(self) -> None:
        self.assertEqual(self.client.resolve_channel("C0123456"), "C0123456")
        self.client.web.conversations_list.assert_not_called()

    def test_channel_name_is_resolved_across_pages(self) -> None:
        self.client.web.conversations_list.side_effect = [
            {
                "channels": [{"id": "C1", "name": "general"}],
                "response_metadata": {"next_cursor": "page-2"},
            },
            {
                "channels": [{"id": "G2", "name": "Bot-Lab"}],
                "response_metadata": {"next_cursor": ""},
            },
        ]

        self.assertEqual(self.client.resolve_channel("#bot-lab"), "G2")
        self.assertEqual(
            self.client.web.conversations_list.call_args_list,
            [
                call(
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor=None,
                ),
                call(
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor="page-2",
                ),
            ],
        )

    def test_post_resolves_name_and_returns_response(self) -> None:
        self.client.web.conversations_list.return_value = {
            "channels": [{"id": "C123", "name": "general"}],
            "response_metadata": {},
        }
        self.client.web.chat_postMessage.return_value = FakeSlackResponse(
            {
                "ok": True,
                "channel": "C123",
                "ts": "1234567890.123456",
            }
        )

        result = self.client.post_message("#general", "hello")

        self.assertTrue(result["ok"])
        self.client.web.chat_postMessage.assert_called_once_with(
            channel="C123", text="hello", thread_ts=None, unfurl_links=False
        )

    def test_reads_top_level_message(self) -> None:
        self.client.web.conversations_history.return_value = {
            "messages": [
                {"ts": "1234567890.123456", "user": "U123", "text": "hello"}
            ]
        }
        url = "https://example.slack.com/archives/C123/p1234567890123456"

        result = self.client.read_message(url)

        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["channel"], "C123")
        self.assertEqual(result["permalink"], url)
        self.client.web.conversations_history.assert_called_once_with(
            channel="C123",
            oldest="1234567890.123456",
            latest="1234567890.123456",
            inclusive=True,
            limit=1,
        )

    def test_reads_thread_reply(self) -> None:
        self.client.web.conversations_replies.return_value = {
            "messages": [
                {"ts": "1234567890.111111", "text": "parent"},
                {"ts": "1234567890.654321", "text": "reply"},
            ],
            "response_metadata": {},
        }
        url = (
            "https://example.slack.com/archives/C123/p1234567890654321"
            "?thread_ts=1234567890.111111&cid=C123"
        )

        result = self.client.read_message(url)

        self.assertEqual(result["text"], "reply")
        self.client.web.conversations_replies.assert_called_once_with(
            channel="C123",
            ts="1234567890.111111",
            oldest="1234567890.654321",
            latest="1234567890.654321",
            inclusive=True,
            limit=1,
        )


if __name__ == "__main__":
    unittest.main()
