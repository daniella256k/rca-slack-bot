"""
Configuration loading for the Slack bot.

Reads a YAML config file (default: config.yaml) and exposes it as simple,
typed Python objects. This file should stay free of any Slack-specific
logic -- its only job is turning YAML into structured config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class SlackConfig:
    bot_token: str
    app_token: Optional[str]
    signing_secret: Optional[str] = None


@dataclass
class DatabaseConfig:
    path: str = "bot_state.db"


@dataclass
class RuleConfig:
    """
    Generic container for a single rule's configuration block.

    `type` selects which Rule subclass handles it (see rules/base.py's
    registry). `options` holds everything else untouched, so each Rule
    implementation is free to define and interpret its own settings
    without this file needing to know about them.
    """

    name: str
    type: str
    enabled: bool
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AppConfig:
    slack: SlackConfig
    database: DatabaseConfig
    poll_interval_seconds: int
    rules: List[RuleConfig]


def load_config(path: Optional[str] = None, *, require_app_token: bool = True) -> AppConfig:
    path = path or os.environ.get("SLACKBOT_CONFIG", "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Config file not found at '{path}'. Copy config.example.yaml to "
            f"config.yaml and fill in your values."
        )

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    slack_raw = raw.get("slack", {})
    try:
        slack = SlackConfig(
            bot_token=slack_raw["bot_token"],
            app_token=slack_raw.get("app_token"),
            signing_secret=slack_raw.get("signing_secret"),
        )
    except KeyError as e:
        raise ValueError(f"Missing required slack config key: {e}") from e
    if require_app_token and not slack.app_token:
        raise ValueError("Missing required slack config key: 'app_token'")

    db_raw = raw.get("database", {})
    database = DatabaseConfig(path=db_raw.get("path", "bot_state.db"))

    rules = []
    for r in raw.get("rules", []):
        options = {k: v for k, v in r.items() if k not in ("name", "type", "enabled")}
        rules.append(
            RuleConfig(
                name=r["name"],
                type=r["type"],
                enabled=r.get("enabled", True),
                options=options,
            )
        )

    return AppConfig(
        slack=slack,
        database=database,
        poll_interval_seconds=raw.get("poll_interval_seconds", 60),
        rules=rules,
    )
