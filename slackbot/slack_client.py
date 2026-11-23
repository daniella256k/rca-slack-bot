"""
Thin wrapper around slack_sdk's WebClient. Keeps Slack API quirks
(permalinks, chat.postMessage kwargs, error handling, etc.) in one place
so the rest of the bot doesn't need to know about them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

logger = logging.getLogger(__name__)

_CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]+$")
_PERMALINK_PATH_RE = re.compile(r"^/archives/([A-Z0-9]+)/p(\d{10,})/?$")


@dataclass(frozen=True)
class MessagePermalink:
    channel: str
    message_ts: str
    thread_ts: Optional[str] = None


def parse_message_permalink(permalink: str) -> MessagePermalink:
    """Turn a copied Slack message permalink into Web API identifiers."""
    parsed = urlparse(permalink.strip().strip("<>"))
    if parsed.scheme != "https" or not (
        parsed.hostname == "slack.com" or (parsed.hostname or "").endswith(".slack.com")
    ):
        raise ValueError("Expected an https://<workspace>.slack.com message permalink")

    match = _PERMALINK_PATH_RE.fullmatch(parsed.path)
    if match is None:
        raise ValueError("Expected a Slack permalink shaped like /archives/<channel>/p<timestamp>")

    channel, compact_ts = match.groups()
    # Slack permalinks remove the decimal from timestamps. A shortened value
    # with only epoch seconds is also accepted, which is handy in tests.
    message_ts = f"{compact_ts[:10]}.{compact_ts[10:] or '000000'}"
    thread_ts = parse_qs(parsed.query).get("thread_ts", [None])[0]
    if thread_ts is not None and re.fullmatch(r"\d{10}\.\d+", thread_ts) is None:
        raise ValueError("The permalink has an invalid thread_ts query parameter")

    return MessagePermalink(channel=channel, message_ts=message_ts, thread_ts=thread_ts)


class SlackClient:
    def __init__(self, bot_token: str):
        self.web = WebClient(token=bot_token)
        # Wait and retry when Slack rate-limits us (HTTP 429), e.g. while
        # paging through a long channel history for /collect.
        self.web.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=3))

    def resolve_channel(self, channel: str) -> str:
        """Resolve #name/name to an ID; copied channel IDs pass through."""
        value = channel.strip()
        if not value:
            raise ValueError("Channel cannot be empty")
        if not value.startswith("#") and _CHANNEL_ID_RE.fullmatch(value):
            return value

        wanted_name = value.removeprefix("#").casefold()
        cursor: Optional[str] = None
        while True:
            try:
                resp = self.web.conversations_list(
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
            except SlackApiError:
                logger.exception("Failed to resolve Slack channel %s", channel)
                raise

            for candidate in resp.get("channels", []):
                if str(candidate.get("name", "")).casefold() == wanted_name:
                    return candidate["id"]

            cursor = resp.get("response_metadata", {}).get("next_cursor") or None
            if cursor is None:
                break

        raise ValueError(
            f"Channel {channel!r} was not found. Check the name and invite the bot "
            "to the channel if it is private."
        )

    def post_message(
        self, channel: str, text: str, thread_ts: Optional[str] = None
    ) -> Dict[str, Any]:
        channel_id = self.resolve_channel(channel)
        try:
            resp = self.web.chat_postMessage(
                channel=channel_id, text=text, thread_ts=thread_ts, unfurl_links=False
            )
            # slack_sdk returns SlackResponse, whose payload lives in `.data`.
            # Iterating SlackResponse directly does not produce (key, value)
            # pairs, so dict(resp) raises *after* Slack has accepted the post.
            return dict(resp.data)
        except SlackApiError:
            logger.exception("Failed to post message to channel %s", channel_id)
            raise

    def read_message(self, permalink: str) -> Dict[str, Any]:
        """Read the exact top-level message or thread reply at a permalink."""
        ref = parse_message_permalink(permalink)
        message: Optional[Dict[str, Any]] = None

        if ref.thread_ts and ref.thread_ts != ref.message_ts:
            resp = self.web.conversations_replies(
                channel=ref.channel,
                ts=ref.thread_ts,
                oldest=ref.message_ts,
                latest=ref.message_ts,
                inclusive=True,
                limit=1,
            )
            message = next(
                (m for m in resp.get("messages", []) if m.get("ts") == ref.message_ts),
                None,
            )
        else:
            resp = self.web.conversations_history(
                channel=ref.channel,
                oldest=ref.message_ts,
                latest=ref.message_ts,
                inclusive=True,
                limit=1,
            )
            message = next(
                (m for m in resp.get("messages", []) if m.get("ts") == ref.message_ts),
                None,
            )

        if message is None:
            raise ValueError(
                "The message was not found. It may have been deleted, or the bot may "
                "not have access to its channel."
            )

        result = dict(message)
        result["channel"] = ref.channel
        result["permalink"] = permalink
        return result

    def get_permalink(self, channel: str, message_ts: str) -> Optional[str]:
        try:
            resp = self.web.chat_getPermalink(channel=channel, message_ts=message_ts)
            return resp.get("permalink")
        except SlackApiError:
            logger.warning("Could not fetch permalink for %s/%s", channel, message_ts)
            return None
