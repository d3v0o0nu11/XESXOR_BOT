"""Invite rewards bookkeeping.

The invite *diffing* is platform work (only Discord exposes invite uses);
this service stores the join ledger and pays out the 10-Coin referral
bonus.  Telegram has no per-user invite tracking, so the adapter simply
never calls it there.
"""

from __future__ import annotations

import time

from xesxor.core.database import Database

REFERRAL_REWARD = 10.0
WELCOME_BONUS = 100.0


class InviteService:
    """Referral join ledger and payouts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def register_join(self, user_id: str, scope_id: str, inviter_id: str | None = None) -> None:
        """Provision a joining user and reward the referrer, if any.

        The joiner starts with :data:`WELCOME_BONUS` Coins.  A valid
        referrer (different, non-bot human) earns
        :data:`REFERRAL_REWARD` Coins.
        """
        now = time.time()
        self._db.execute(
            "INSERT INTO users (user_id, scope_id, coins, joined_timestamp) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, scope_id) DO NOTHING",
            (user_id, scope_id, WELCOME_BONUS, now),
        )
        if inviter_id and inviter_id != user_id:
            self._db.execute(
                "INSERT INTO users (user_id, scope_id, coins, joined_timestamp) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, scope_id) "
                "DO UPDATE SET coins = coins + ?",
                (inviter_id, scope_id, WELCOME_BONUS + REFERRAL_REWARD, now, REFERRAL_REWARD),
            )
