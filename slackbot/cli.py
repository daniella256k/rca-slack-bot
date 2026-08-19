"""Small developer CLI for exercising the bot's Slack access.

Examples:
    python -m slackbot.cli post "#general" "Hello from the test bot"
    python -m slackbot.cli read "https://example.slack.com/archives/C123/p1234567890123456"
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, Optional, Sequence

from slack_sdk.errors import SlackApiError

from .config import load_config
from .slack_client import SlackClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Developer commands for the Slack bot")
    parser.add_argument(
        "--config",
        help="config file (default: $SLACKBOT_CONFIG or config.yaml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    post_parser = subparsers.add_parser("post", help="post a message to a channel")
    post_parser.add_argument("channel", help="channel name (#general/general) or channel ID")
    post_parser.add_argument("message", help="message text")

    read_parser = subparsers.add_parser("read", help="read a message from its permalink")
    read_parser.add_argument("permalink", help="hard Slack message permalink")
    return parser


def _print_json(value: Dict[str, Any], *, stream: Any = None) -> None:
    print(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        file=stream or sys.stdout,
    )


def _slack_error(exc: SlackApiError) -> str:
    error = exc.response.get("error") if exc.response is not None else None
    return f"Slack API error: {error or exc}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # Avoid logging a traceback for an expected API/permission error. The CLI
    # reports a compact JSON error below instead.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    try:
        cfg = load_config(args.config, require_app_token=False)
        slack = SlackClient(cfg.slack.bot_token)

        if args.command == "post":
            posted = slack.post_message(args.channel, args.message)
            channel = posted.get("channel")
            ts = posted.get("ts")
            output: Dict[str, Any] = {
                "ok": bool(posted.get("ok", True)),
                "channel": channel,
                "ts": ts,
                "message": posted.get("message"),
            }
            if channel and ts:
                output["permalink"] = slack.get_permalink(channel, ts)
            _print_json(output)
            return 0

        _print_json(slack.read_message(args.permalink))
        return 0
    except SlackApiError as exc:
        _print_json({"ok": False, "error": _slack_error(exc)}, stream=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        _print_json({"ok": False, "error": str(exc)}, stream=sys.stderr)
        return 1
    finally:
        logging.disable(previous_logging_disable)


if __name__ == "__main__":
    raise SystemExit(main())
