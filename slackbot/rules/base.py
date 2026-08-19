"""
Base class + registry for pluggable "rules".

A Rule watches for Slack events (currently: new messages, emoji
reactions) and decides what to do about them. To add a brand-new
behavior to the bot later, subclass Rule, implement its methods, and
register it under a `type` name with @register_rule(...). Reference that
`type` from a block in config.yaml -- no changes to app.py are needed.

Lifecycle:
  1. on_message(event)     -- called for every 'message' event seen by
                               the bot; a rule decides whether to start
                               tracking it (i.e. create a Watch).
  2. on_reaction(event)    -- called for every 'reaction_added' event; a
                               rule decides whether it should cancel a
                               Watch it's tracking.
  3. on_notification(...)  -- called by the scheduler when a previously
                               scheduled notification for one of this
                               rule's watches comes due (e.g. "send the
                               1-hour warning").

Rules are intentionally *not* required to be countdown-based -- that's
just what CountdownReactionRule implements. A future rule type could,
for example, react to a keyword instantly with no timer at all, simply
by leaving on_notification a no-op and doing its work in on_message.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Type

from ..slack_client import SlackClient
from ..storage import Notification, Storage, Watch

RULE_REGISTRY: Dict[str, Type["Rule"]] = {}


def register_rule(type_name: str):
    """Class decorator: @register_rule("countdown_reaction")"""

    def wrapper(cls: Type["Rule"]) -> Type["Rule"]:
        RULE_REGISTRY[type_name] = cls
        return cls

    return wrapper


class Rule(ABC):
    def __init__(self, name: str, options: dict, storage: Storage, slack: SlackClient):
        self.name = name
        self.options = options
        self.storage = storage
        self.slack = slack

    @abstractmethod
    def on_message(self, event: dict) -> None:
        """Inspect an incoming message event; start a watch if relevant."""

    @abstractmethod
    def on_reaction(self, event: dict) -> None:
        """Inspect a reaction_added event; cancel a watch if relevant."""

    @abstractmethod
    def on_notification(self, notification: Notification, watch: Watch) -> None:
        """Handle a due notification (e.g. post a warning message)."""
