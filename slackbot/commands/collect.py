"""
/collect -- export the sales-record channel to a CSV, delivered by DM.

    /collect 2026-10-1               -> from Oct 1 (Chicago time) until now
    /collect 2026-10-1 2026-10-31    -> Oct 1 through the end of Oct 31

Every line of every top-level message becomes its own row with exactly
three columns: sender, timestamp, line_content. No interpretation of the
content happens here -- cleaning up chatter is left to a human in the
spreadsheet.

Safety: this command never posts in a channel. The only visible output is
(a) an ephemeral reply that only the person who ran the command can see,
and (b) the CSV file sent to that same person's DM with the bot. Only
user IDs listed in `allowed_users` can run it; an empty list means nobody.

Config (config.yaml):

    commands:
      collect:
        enabled: true
        channel: "C04HFLUHX6H"
        timezone: "America/Chicago"
        allowed_users: ["U0123456", ...]
"""
from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CSV_HEADER = ("sender", "timestamp", "line_content")
USAGE = (
    "Usage: `/collect START [END]` with dates as YYYY-MM-DD, e.g. "
    "`/collect 2026-10-1` (until now) or `/collect 2026-10-1 2026-10-31`."
)

# Top-level messages we export. Everything else (joins, leaves, topic
# changes, bot posts, ...) is skipped. thread_broadcast = a thread reply
# that was also sent to the channel, so it shows in the channel.
_INCLUDED_SUBTYPES = {None, "thread_broadcast", "file_share"}

_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_USER_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_MENTION_RE = re.compile(r"<#([CG][A-Z0-9]+)(?:\|([^>]*))?>")
_SUBTEAM_RE = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|([^>]*))?>")
_SPECIAL_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_LINK_RE = re.compile(r"<([^<>|]+)(?:\|([^<>]*))?>")


@dataclass(frozen=True)
class CollectSettings:
    channel: str
    timezone: str = "America/Chicago"
    allowed_users: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CollectSettings":
        channel = raw.get("channel")
        if not channel:
            raise ValueError("commands.collect.channel is required")
        users = tuple(str(u).strip().lstrip("#") for u in raw.get("allowed_users") or [])
        return cls(
            channel=str(channel),
            timezone=str(raw.get("timezone", "America/Chicago")),
            allowed_users=users,
        )


# --------------------------------------------------------------------------
# Pure helpers (no Slack calls) -- easy to unit test.
# --------------------------------------------------------------------------

def _parse_date(value: str) -> date:
    match = _DATE_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"`{value}` isn't a date like 2026-10-1.")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        raise ValueError(f"`{value}` isn't a real calendar date.") from None


def parse_date_range(
    text: str, tz: ZoneInfo, now: Optional[datetime] = None
) -> Tuple[datetime, datetime]:
    """Turn the command text into an [oldest, latest) pair of aware datetimes.

    START is midnight local time; END (optional) is inclusive, so the range
    runs to midnight after it. Without END the range runs until now.
    """
    now = now or datetime.now(tz)
    parts = text.split()
    if not parts or len(parts) > 2:
        raise ValueError(USAGE)

    start = datetime.combine(_parse_date(parts[0]), datetime.min.time(), tzinfo=tz)
    if len(parts) == 2:
        end = datetime.combine(
            _parse_date(parts[1]) + timedelta(days=1), datetime.min.time(), tzinfo=tz
        )
    else:
        end = now

    if start >= now:
        raise ValueError("The start date is in the future.")
    if start >= end:
        raise ValueError("The start date must be on or before the end date.")
    return start, min(end, now)


def clean_text(text: str, user_name: Callable[[str], str]) -> str:
    """Convert Slack's markup into what a person sees in the client."""
    text = _USER_MENTION_RE.sub(lambda m: "@" + user_name(m.group(1)), text)
    text = _CHANNEL_MENTION_RE.sub(lambda m: "#" + (m.group(2) or m.group(1)), text)
    text = _SUBTEAM_RE.sub(lambda m: m.group(1) or "@group", text)
    text = _SPECIAL_RE.sub(lambda m: "@" + m.group(1), text)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    # Slack escapes exactly these three characters.
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def split_lines(text: str) -> List[str]:
    """One entry per non-blank line, whitespace trimmed."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def build_rows(
    messages: Iterable[Dict[str, Any]],
    tz: ZoneInfo,
    user_name: Callable[[str], str],
) -> List[Tuple[str, str, str]]:
    """Messages (any order) -> chronological (sender, timestamp, line) rows."""
    rows: List[Tuple[float, str, str, str]] = []
    for msg in messages:
        if msg.get("subtype") not in _INCLUDED_SUBTYPES:
            continue
        # conversations.history only returns top-level messages plus
        # broadcasts, but be defensive about plain thread replies.
        if msg.get("thread_ts") not in (None, msg.get("ts")) and msg.get("subtype") != "thread_broadcast":
            continue

        ts = float(msg["ts"])
        stamp = datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S")
        uid = msg.get("user")
        sender = user_name(uid) if uid else (msg.get("username") or msg.get("bot_id") or "unknown")
        for line in split_lines(clean_text(msg.get("text") or "", user_name)):
            rows.append((ts, sender, stamp, line))

    rows.sort(key=lambda r: r[0])  # stable: lines of one message keep their order
    return [(sender, stamp, line) for _, sender, stamp, line in rows]


def to_csv(rows: Sequence[Tuple[str, str, str]]) -> str:
    """CSV text with a UTF-8 BOM so Excel shows emoji/accents correctly."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    writer.writerows(rows)
    return "﻿" + buf.getvalue()


# --------------------------------------------------------------------------
# Slack-facing pieces.
# --------------------------------------------------------------------------

class UserNames:
    """Caches user ID -> display name lookups for one export run."""

    def __init__(self, web):
        self._web = web
        self._cache: Dict[str, str] = {}

    def __call__(self, user_id: str) -> str:
        if user_id not in self._cache:
            name = user_id
            try:
                user = self._web.users_info(user=user_id)["user"]
                profile = user.get("profile") or {}
                name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or user.get("real_name")
                    or user.get("name")
                    or user_id
                )
            except Exception:
                logger.warning("Could not look up Slack user %s", user_id)
            self._cache[user_id] = name
        return self._cache[user_id]


def fetch_messages(web, channel: str, oldest: datetime, latest: datetime) -> List[Dict[str, Any]]:
    """All top-level messages in [oldest, latest), following pagination."""
    messages: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        resp = web.conversations_history(
            channel=channel,
            oldest=f"{oldest.timestamp():.6f}",
            latest=f"{latest.timestamp():.6f}",
            limit=200,
            cursor=cursor,
        )
        messages.extend(resp.get("messages", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return messages


def run_collect(web, settings: CollectSettings, user_id: str, text: str,
                now: Optional[datetime] = None) -> str:
    """Do the export and DM it to `user_id`. Returns a short status message.

    Raises ValueError for bad input (shown to the user as-is).
    """
    tz = ZoneInfo(settings.timezone)
    start, end = parse_date_range(text, tz, now)

    names = UserNames(web)
    rows = build_rows(fetch_messages(web, settings.channel, start, end), tz, names)

    end_label = (end - timedelta(seconds=1)).strftime("%Y-%m-%d")
    period = f"{start:%Y-%m-%d} to {end_label}"
    if not rows:
        return f"No messages found in <#{settings.channel}> from {period}."

    # Open (or reuse) the bot's DM with the requester. The file goes ONLY
    # there -- never to the source channel or wherever the command was run.
    dm_channel = web.conversations_open(users=user_id)["channel"]["id"]
    filename = f"sales-record_{start:%Y-%m-%d}_to_{end_label}.csv"
    web.files_upload_v2(
        channel=dm_channel,
        content=to_csv(rows),
        filename=filename,
        title=filename,
        initial_comment=f"{len(rows)} lines from <#{settings.channel}>, {period}.",
    )
    return f"Done: sent {len(rows)} lines ({period}) to your DMs with me."


def register(bolt_app, settings: CollectSettings, web) -> None:
    """Attach the /collect slash command to a Bolt app."""

    @bolt_app.command("/collect")
    def handle_collect(ack, command, respond):
        user_id = command.get("user_id", "")
        if user_id not in settings.allowed_users:
            logger.info("/collect refused for user %s", user_id)
            ack(text="Sorry, you're not on the list of people allowed to run /collect.")
            return

        text = (command.get("text") or "").strip()
        if not text:
            ack(text=USAGE)
            return

        # ack() replies ephemerally (only the requester sees it). Bolt sends
        # the ack immediately; the slow export continues after it.
        ack(text="Collecting... I'll DM you the CSV shortly.")
        logger.info("/collect %r requested by %s", text, user_id)
        try:
            message = run_collect(web, settings, user_id, text)
        except ValueError as exc:
            message = f"{exc}\n{USAGE}" if str(exc) != USAGE else USAGE
        except Exception:
            logger.exception("/collect failed for user %s", user_id)
            message = "Something went wrong collecting the messages. The error is in the bot's logs."
        respond(text=message, response_type="ephemeral", replace_original=False)
