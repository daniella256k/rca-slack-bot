from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from slackbot.cli import main


class CliTests(unittest.TestCase):
    @patch("slackbot.cli.SlackClient")
    @patch("slackbot.cli.load_config")
    def test_post_outputs_json(self, load_config: Mock, slack_class: Mock) -> None:
        load_config.return_value = SimpleNamespace(
            slack=SimpleNamespace(bot_token="xoxb-test")
        )
        slack = slack_class.return_value
        slack.post_message.return_value = {
            "ok": True,
            "channel": "C123",
            "ts": "1234567890.123456",
            "message": {"text": "hello"},
        }
        slack.get_permalink.return_value = "https://example.slack.com/archives/C123/p123"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(["--config", "config.dev.yaml", "post", "#general", "hello"])

        self.assertEqual(exit_code, 0)
        load_config.assert_called_once_with(
            "config.dev.yaml", require_app_token=False
        )
        slack.post_message.assert_called_once_with("#general", "hello")
        self.assertEqual(json.loads(stdout.getvalue())["channel"], "C123")

    @patch("slackbot.cli.load_config", side_effect=FileNotFoundError("missing config"))
    def test_expected_error_is_json_on_stderr(self, load_config: Mock) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            exit_code = main(["read", "https://example.slack.com/archives/C1/p1234567890"])

        self.assertEqual(exit_code, 1)
        self.assertFalse(json.loads(stderr.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
