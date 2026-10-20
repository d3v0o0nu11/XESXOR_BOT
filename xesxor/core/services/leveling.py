"""XP, levels and activity rewards.

* +15 XP per message, rate-limited to one award per user per 60 seconds;
* level-up when accumulated XP crosses ``level * 100``;
* +1 Coin every 100 messages sent;
* voice time pays 5 Coins/hour plus XP (Discord only — adapters call in).
"""

from __future__ import annotations

import time
from typing import Optional

from xesxor.core.database import Database
from xesxor.core.models import LevelUpResult, MessageReward

XP_PER_MESSAGE = 15
XP_COOLDOWN_SECONDS = 60
MESSAGE_MILESTONE = 100
MESSAGE_MILESTONE_COINS = 1.0
VOICE_COINS_PER_HOUR = 5.0
VOICE_XP_PER_MINUTE = 1


class LevelingService:
    """Progression tracking for chat and voice activity."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._last_xp: dict[tuple[str, str], float] = {}

    def record_message(self, user_id: str, scope_id: str) -> tuple[Optional[LevelUpResult], Optional[MessageReward]]:
        """Record one message; returns (level_up, milestone_reward).

        Either element may be ``None``.  Callers must provision the profile
        (via :class:`EconomyService`) before or through this call.
        """
        now = time.time()
        self._db.execute(
            "UPDATE users SET messages = messages + 1 WHERE user_id = ? AND scope_id = ?",
            (user_id, scope_id),
        )
        row = self._db.fetchone(
            "SELECT messages, xp, level FROM users WHERE user_id = ? AND scope_id = ?",
            (user_id, scope_id),
        )
        if row is None:
            return None, None

        milestone: Optional[MessageReward] = None
        messages = row["messages"]
        if messages > 0 and messages % MESSAGE_MILESTONE == 0:
            self._db.execute(
                "UPDATE users SET coins = coins + ? WHERE user_id = ? AND scope_id = ?",
                (MESSAGE_MILESTONE_COINS, user_id, scope_id),
            )
            milestone = MessageReward(messages=messages, coins=MESSAGE_MILESTONE_COINS)

        level_up: Optional[LevelUpResult] = None
        key = (user_id, scope_id)
        if now - self._last_xp.get(key, 0.0) > XP_COOLDOWN_SECONDS:
            self._last_xp[key] = now
            xp = row["xp"] + XP_PER_MESSAGE
            level = row["level"]
            if xp >= level * 100:
                level += 1
                xp -= (level - 1) * 100
                level_up = LevelUpResult(new_level=level)
            self._db.execute(
                "UPDATE users SET xp = ?, level = ? WHERE user_id = ? AND scope_id = ?",
                (xp, level, user_id, scope_id),
            )
        return level_up, milestone

    def record_voice_minutes(self, user_id: str, scope_id: str, minutes: int) -> None:
        """Credit *minutes* of voice-chat time (coins + XP)."""
        if minutes <= 0:
            return
        earned = (minutes / 60.0) * VOICE_COINS_PER_HOUR
        self._db.execute(
            "INSERT INTO users (user_id, scope_id, xp, coins, voice_minutes, joined_timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, scope_id) DO UPDATE SET "
            "xp = xp + ?, coins = coins + ?, voice_minutes = voice_minutes + ?",
            (
                user_id, scope_id, minutes * VOICE_XP_PER_MINUTE, earned, minutes, time.time(),
                minutes * VOICE_XP_PER_MINUTE, earned, minutes,
            ),
        )
