from __future__ import annotations

import csv
import io
import unittest
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from slackbot.commands import collect
from slackbot.commands.collect import (
    CollectSettings,
    build_rows,
    clean_text,
    parse_date_range,
    run_collect,
    split_lines,
    to_csv,
)

CHI = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 23, 15, 0, tzinfo=CHI)
SALES = "C04HFLUHX6H"
SETTINGS = CollectSettings(channel=SALES, allowed_users=("U06T46PT3H9",))


def ts(year, month, day, hour=12, minute=0) -> str:
    return f"{datetime(year, month, day, hour, minute, tzinfo=CHI).timestamp():.6f}"


def names(uid: str) -> str:
    return {"U1": "Luke", "U2": "Clair Miller", "U06MRHPEPB6": "Keith"}.get(uid, uid)


class ParseDateRangeTests(unittest.TestCase):
    def test_start_only_runs_until_now(self) -> None:
        start, end = parse_date_range("2026-10-1", CHI, NOW)
        self.assertEqual(start, datetime(2026, 10, 1, tzinfo=CHI))
        self.assertEqual(end, NOW)

    def test_end_date_is_inclusive(self) -> None:
        start, end = parse_date_range("2026-10-01 2026-10-31", CHI, NOW)
        self.assertEqual(start, datetime(2026, 10, 1, tzinfo=CHI))
        self.assertEqual(end, datetime(2026, 11, 1, tzinfo=CHI))

    def test_end_in_future_is_capped_at_now(self) -> None:
        _, end = parse_date_range("2026-11-1 2026-12-31", CHI, NOW)
        self.assertEqual(end, NOW)

    def test_same_day_range(self) -> None:
        start, end = parse_date_range("2026-11-14 2026-11-14", CHI, NOW)
        self.assertEqual(end - start, datetime(2026, 11, 15, tzinfo=CHI) - start)

    def test_rejects_bad_input(self) -> None:
        for text in ["", "10/1/2026", "2026-13-01", "2026-02-30", "2026-10-1 x", "a b c"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_date_range(text, CHI, NOW)

    def test_rejects_future_start_and_reversed_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "future"):
            parse_date_range("2026-12-01", CHI, NOW)
        with self.assertRaisesRegex(ValueError, "on or before"):
            parse_date_range("2026-10-31 2026-10-01", CHI, NOW)


class TextTests(unittest.TestCase):
    def test_clean_text_converts_slack_markup(self) -> None:
        raw = (
            "$10 Venmo for TC 28 ($5 donation) to Keith <@U06MRHPEPB6> "
            "in <#C123|sales-record> &amp; <https://x.org|site> <!here> 2 &lt; 3"
        )
        self.assertEqual(
            clean_text(raw, names),
            "$10 Venmo for TC 28 ($5 donation) to Keith @Keith "
            "in #sales-record & site @here 2 < 3",
        )

    def test_split_lines_drops_blank_lines(self) -> None:
        self.assertEqual(
            split_lines("$4 cashapp Marxism\n$5 cashapp TC25\n\n  $5 cashapp TC26  \n"),
            ["$4 cashapp Marxism", "$5 cashapp TC25", "$5 cashapp TC26"],
        )


class BuildRowsTests(unittest.TestCase):
    def test_one_row_per_line_in_time_order(self) -> None:
        messages = [  # newest first, as conversations.history returns them
            {"ts": ts(2026, 11, 14, 18, 41), "user": "U1", "text": "$5 tc27 + $1 donation cash"},
            {"type": "message", "subtype": "channel_join", "ts": ts(2026, 11, 14, 18, 0),
             "user": "U9", "text": "<@U9> has joined the channel"},
            {"ts": ts(2026, 10, 11, 9, 5), "user": "U2",
             "text": "$5 venmo tc 27 \n$5 cash tc 27 (luke will venmo)"},
        ]
        rows = build_rows(messages, CHI, names)
        self.assertEqual(rows, [
            ("Clair Miller", "2026-10-11 09:05:00", "$5 venmo tc 27"),
            ("Clair Miller", "2026-10-11 09:05:00", "$5 cash tc 27 (luke will venmo)"),
            ("Luke", "2026-11-14 18:41:00", "$5 tc27 + $1 donation cash"),
        ])

    def test_skips_plain_thread_replies_but_keeps_broadcasts(self) -> None:
        parent = ts(2026, 11, 7)
        messages = [
            {"ts": parent, "thread_ts": parent, "user": "U1", "text": "5 Cash paper"},
            {"ts": ts(2026, 11, 7, 13), "thread_ts": parent, "user": "U2", "text": "TC28?"},
            {"ts": ts(2026, 11, 7, 14), "thread_ts": parent, "subtype": "thread_broadcast",
             "user": "U1", "text": "Paper was tc28"},
        ]
        lines = [row[2] for row in build_rows(messages, CHI, names)]
        self.assertEqual(lines, ["5 Cash paper", "Paper was tc28"])

    def test_csv_has_bom_header_and_quotes_commas(self) -> None:
        text = to_csv([("Luke", "2026-11-14 18:41:00", '$20 "classics, vol 1"')])
        self.assertTrue(text.startswith("﻿"))
        parsed = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
        self.assertEqual(parsed, [
            ["sender", "timestamp", "line_content"],
            ["Luke", "2026-11-14 18:41:00", '$20 "classics, vol 1"'],
        ])


class RunCollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.web = Mock()
        self.web.users_info.return_value = {"user": {"profile": {"display_name": "Luke"}}}
        self.web.conversations_open.return_value = {"channel": {"id": "D_REQUESTER"}}
        self.web.conversations_history.side_effect = [
            {"messages": [{"ts": ts(2026, 11, 2), "user": "U1", "text": "$5 Venmo TC28"}],
             "response_metadata": {"next_cursor": "page-2"}},
            {"messages": [{"ts": ts(2026, 11, 1), "user": "U1", "text": "$5 cash TC28"}],
             "response_metadata": {"next_cursor": ""}},
        ]

    def test_pages_history_and_dms_only_the_requester(self) -> None:
        status = run_collect(self.web, SETTINGS, "U06T46PT3H9", "2026-11-1", now=NOW)

        self.assertEqual(self.web.conversations_history.call_count, 2)
        first = self.web.conversations_history.call_args_list[0].kwargs
        self.assertEqual(first["channel"], SALES)
        self.assertEqual(float(first["oldest"]), datetime(2026, 11, 1, tzinfo=CHI).timestamp())
        self.assertEqual(self.web.conversations_history.call_args_list[1].kwargs["cursor"], "page-2")

        self.web.conversations_open.assert_called_once_with(users="U06T46PT3H9")
        upload = self.web.files_upload_v2.call_args.kwargs
        self.assertEqual(upload["channel"], "D_REQUESTER")
        self.assertEqual(upload["filename"], "sales-record_2026-11-01_to_2026-11-23.csv")
        self.assertIn("$5 cash TC28", upload["content"])
        self.assertLess(upload["content"].index("$5 cash TC28"), upload["content"].index("$5 Venmo TC28"))
        self.web.chat_postMessage.assert_not_called()
        self.assertIn("2 lines", status)

    def test_no_messages_sends_nothing(self) -> None:
        self.web.conversations_history.side_effect = [{"messages": []}]
        status = run_collect(self.web, SETTINGS, "U06T46PT3H9", "2026-11-1", now=NOW)
        self.assertIn("No messages", status)
        self.web.conversations_open.assert_not_called()
        self.web.files_upload_v2.assert_not_called()


class FakeBolt:
    def __init__(self):
        self.handlers = {}

    def command(self, name):
        def decorator(fn):
            self.handlers[name] = fn
            return fn
        return decorator


class HandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FakeBolt()
        self.web = Mock()
        collect.register(self.app, SETTINGS, self.web)
        self.handler = self.app.handlers["/collect"]
        self.ack, self.respond = Mock(), Mock()

    def call(self, user_id: str, text: str) -> None:
        self.handler(ack=self.ack, respond=self.respond,
                     command={"user_id": user_id, "text": text, "channel_id": SALES})

    def test_unlisted_user_is_refused_without_any_slack_calls(self) -> None:
        self.call("U_SOMEONE_ELSE", "2026-10-1")
        self.assertIn("not on the list", self.ack.call_args.kwargs["text"])
        self.assertEqual(self.web.method_calls, [])
        self.respond.assert_not_called()

    def test_empty_text_shows_usage(self) -> None:
        self.call("U06T46PT3H9", "")
        self.assertIn("Usage", self.ack.call_args.kwargs["text"])
        self.assertEqual(self.web.method_calls, [])

    def test_bad_date_is_reported_ephemerally(self) -> None:
        self.call("U06T46PT3H9", "October 1")
        self.ack.assert_called_once()
        kwargs = self.respond.call_args.kwargs
        self.assertEqual(kwargs["response_type"], "ephemeral")
        self.assertIn("Usage", kwargs["text"])
        self.web.files_upload_v2.assert_not_called()

    def test_unexpected_error_is_reported_ephemerally(self) -> None:
        self.web.conversations_history.side_effect = RuntimeError("boom")
        self.call("U06T46PT3H9", "2026-10-1")
        kwargs = self.respond.call_args.kwargs
        self.assertEqual(kwargs["response_type"], "ephemeral")
        self.assertIn("went wrong", kwargs["text"])


class SettingsTests(unittest.TestCase):
    def test_from_dict_strips_hash_prefix_and_requires_channel(self) -> None:
        s = CollectSettings.from_dict({"channel": SALES, "allowed_users": ["#U054X4A8UMP", "U1 "]})
        self.assertEqual(s.allowed_users, ("U054X4A8UMP", "U1"))
        with self.assertRaises(ValueError):
            CollectSettings.from_dict({"allowed_users": ["U1"]})


if __name__ == "__main__":
    unittest.main()
