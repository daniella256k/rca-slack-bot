"""
Entry point. Wires config -> storage -> slack client -> rules -> Bolt app
-> scheduler, then starts listening over Socket Mode.

Run with:  python -m slackbot.app
"""
from __future__ import annotations

import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import load_config
from .rules.base import RULE_REGISTRY

# Importing a rule module registers its `type` in RULE_REGISTRY (see the
# @register_rule decorator in rules/base.py). Import any future rule
# modules here too -- that's the only change app.py needs when you add
# a new rule type.
from .rules import countdown_reaction  # noqa: F401

from .scheduler import NotificationScheduler
from .slack_client import SlackClient
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_rules(cfg, storage: Storage, slack: SlackClient) -> dict:
    rules = {}
    for rule_cfg in cfg.rules:
        if not rule_cfg.enabled:
            logger.info("Skipping disabled rule '%s'", rule_cfg.name)
            continue
        rule_cls = RULE_REGISTRY.get(rule_cfg.type)
        if rule_cls is None:
            raise ValueError(
                f"Unknown rule type '{rule_cfg.type}' for rule '{rule_cfg.name}'. "
                f"Known types: {list(RULE_REGISTRY)}"
            )
        rules[rule_cfg.name] = rule_cls(rule_cfg.name, rule_cfg.options, storage, slack)
        logger.info("Loaded rule '%s' (type=%s)", rule_cfg.name, rule_cfg.type)
    return rules


def main() -> None:
    cfg = load_config()
    storage = Storage(cfg.database.path)
    slack = SlackClient(cfg.slack.bot_token)
    rules = build_rules(cfg, storage, slack)

    bolt_app = App(token=cfg.slack.bot_token)

    @bolt_app.event("message")
    def handle_message(event):
        for rule in rules.values():
            try:
                rule.on_message(event)
            except Exception:
                logger.exception("Rule '%s' failed handling a message event", rule.name)

    @bolt_app.event("reaction_added")
    def handle_reaction(event):
        for rule in rules.values():
            try:
                rule.on_reaction(event)
            except Exception:
                logger.exception("Rule '%s' failed handling a reaction event", rule.name)

    scheduler = NotificationScheduler(storage, rules, cfg.poll_interval_seconds)
    scheduler.start()

    handler = SocketModeHandler(bolt_app, cfg.slack.app_token)
    logger.info("Starting Slack bot (Socket Mode)...")
    try:
        handler.start()
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
