"""Moderation service: warnings with automatic escalation.

Strike policy (ported from v1):
* 3 strikes → 1-hour timeout;
* 5 strikes → kick.

The service decides *what* should happen; the platform adapter performs the
actual timeout/kick using its native API.
"""

from __future__ import annotations

import time

from xesxor.core.database import Database
from xesxor.core.models import WarnResult

TIMEOUT_AT_STRIKES = 3
KICK_AT_STRIKES = 5
TIMEOUT_HOURS = 1


class ModerationService:
    """Strike tracking and escalation policy."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def warn(
        self, target_id: str, scope_id: str, moderator_id: str, reason: str
    ) -> WarnResult:
        """Record a warning and compute the escalation action, if any.

        Args:
            target_id: User being warned.
            scope_id: Guild/chat scope.
            moderator_id: User issuing the warning.
            reason: Free-form reason stored with the strike.

        Returns:
            A :class:`WarnResult` with the strike count and the escalation
            action the adapter should apply (``timeout``/``kick``/None).
        """
        self._db.execute(
            "INSERT INTO warnings (user_id, scope_id, moderator_id, reason, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (target_id, scope_id, moderator_id, reason, time.time()),
        )
        row = self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM warnings WHERE user_id = ? AND scope_id = ?",
            (target_id, scope_id),
        )
        strikes = int(row["cnt"]) if row else 1

        action = None
        hours = 0
        if strikes >= KICK_AT_STRIKES:
            action = "kick"
        elif strikes >= TIMEOUT_AT_STRIKES:
            action = "timeout"
            hours = TIMEOUT_HOURS
        return WarnResult(strikes=strikes, action=action, action_hours=hours)

    def strike_count(self, target_id: str, scope_id: str) -> int:
        """Return the number of strikes a user holds in this scope."""
        row = self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM warnings WHERE user_id = ? AND scope_id = ?",
            (target_id, scope_id),
        )
        return int(row["cnt"]) if row else 0
