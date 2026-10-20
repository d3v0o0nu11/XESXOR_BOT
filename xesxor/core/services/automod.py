"""Automod: text rule evaluation shared by every platform.

Rules enforce the community guidelines: hate speech/slurs, NSFW terms,
invite-link advertising, Discord token leaks, caps spam and rapid repeat
spam.  Exemptions (staff roles, designated sections) are decided by the
adapter, which knows how to inspect platform roles — this service only
classifies message text.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from xesxor.core.models import Violation

BAD_WORDS: tuple[str, ...] = (
    "nigger", "nigga", "faggot", "retard", "cunt", "kys", "nude", "nsfw", "gore",
)
INVITE_REGEX = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/\w+", re.IGNORECASE)
TOKEN_REGEX = re.compile(r"[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27}")

CAPS_MIN_LENGTH = 20
CAPS_THRESHOLD = 0.75
REPEAT_WINDOW_SECONDS = 15.0
REPEAT_LIMIT = 3

TOKEN_NOTICE = (
    "Your message was removed for containing a sensitive Discord token "
    "(Rule 13: Protect Your Account). Please secure your account immediately!"
)


class AutomodService:
    """Stateless text classification plus a small repeat-spam cache."""

    def __init__(self) -> None:
        # (user_id, scope_id) -> list of {'content', 'time'}
        self._recent: dict[tuple[str, str], list[dict]] = {}

    def check(self, user_id: str, scope_id: str, text: str) -> Optional[Violation]:
        """Classify one message.

        Args:
            user_id: Author id.
            scope_id: Guild/chat scope (keys the repeat-spam cache).
            text: Raw message text.

        Returns:
            A :class:`Violation` when a rule matched, otherwise ``None``.
        """
        lowered = text.lower()

        if any(word in lowered for word in BAD_WORDS):
            return Violation(rule="hate_speech", action="delete")

        if INVITE_REGEX.search(text):
            return Violation(rule="invite_link", action="delete")

        if TOKEN_REGEX.search(text):
            return Violation(rule="token_leak", action="delete_notify", notice=TOKEN_NOTICE)

        if len(text) > CAPS_MIN_LENGTH:
            uppercase = sum(1 for char in text if char.isupper())
            if uppercase / len(text) > CAPS_THRESHOLD:
                return Violation(rule="caps_spam", action="delete")

        if self._is_repeat_spam(user_id, scope_id, text):
            return Violation(rule="repeat_spam", action="delete")

        return None

    def _is_repeat_spam(self, user_id: str, scope_id: str, text: str) -> bool:
        """Detect the same message posted ``REPEAT_LIMIT`` times in the window."""
        key = (user_id, scope_id)
        now = time.time()
        history = [
            entry
            for entry in self._recent.get(key, [])
            if now - entry["time"] < REPEAT_WINDOW_SECONDS
        ]
        matching = sum(1 for entry in history if entry["content"] == text)
        history.append({"content": text, "time": now})
        self._recent[key] = history[-20:]
        return matching + 1 >= REPEAT_LIMIT

    def exempt_placeholders(self) -> tuple[str, ...]:
        """Category/channel name fragments exempt from automod (informational)."""
        return ("team",)
