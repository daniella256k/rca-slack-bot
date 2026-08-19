from __future__ import annotations

import unittest
from unittest.mock import mock_open, patch

from slackbot.config import load_config


class ConfigTests(unittest.TestCase):
    @patch("slackbot.config.os.path.exists", return_value=True)
    def test_cli_config_can_omit_app_token(self, exists) -> None:
        contents = 'slack:\n  bot_token: "xoxb-test"\n'

        with patch("builtins.open", mock_open(read_data=contents)):
            config = load_config("config.dev.yaml", require_app_token=False)

        self.assertEqual(config.slack.bot_token, "xoxb-test")
        self.assertIsNone(config.slack.app_token)

    @patch("slackbot.config.os.path.exists", return_value=True)
    def test_full_app_still_requires_app_token(self, exists) -> None:
        contents = 'slack:\n  bot_token: "xoxb-test"\n'

        with patch("builtins.open", mock_open(read_data=contents)):
            with self.assertRaisesRegex(ValueError, "app_token"):
                load_config("config.dev.yaml")


if __name__ == "__main__":
    unittest.main()
