"""Community features: suggestions, events, signal questions, tickets, roles.

All persistence lives here; the adapters render panels, channels and inline
keyboards around these records.
"""

from __future__ import annotations

import time
from typing import Optional

from xesxor.core.database import Database
from xesxor.core.errors import CommandError
from xesxor.core.models import TicketRecord


class CommunityService:
    """Suggestions, events, weekly-signal questions, tickets and tag roles."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    def add_suggestion(self, user_id: str, scope_id: str, content: str) -> int:
        """Store a suggestion and return its id."""
        content = content.strip()
        if not content:
            raise CommandError("Suggestion text cannot be empty.")
        cursor = self._db.execute(
            "INSERT INTO suggestions (user_id, scope_id, content, status, created_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (user_id, scope_id, content[:1900], time.time()),
        )
        return int(cursor.lastrowid or 0)

    def add_event(self, user_id: str, scope_id: str, title: str, time_str: str, description: str) -> int:
        """Store a scheduled community event and return its id."""
        cursor = self._db.execute(
            "INSERT INTO community_events "
            "(user_id, scope_id, title, time_str, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, scope_id, title[:150], time_str[:100], description[:1900], time.time()),
        )
        return int(cursor.lastrowid or 0)

    def add_signal_question(self, user_id: str, scope_id: str, question: str) -> int:
        """Queue a question for the weekly live signal and return its id."""
        question = question.strip()
        if not question:
            raise CommandError("Question cannot be empty.")
        cursor = self._db.execute(
            "INSERT INTO signal_questions (user_id, scope_id, question, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, scope_id, question[:1900], time.time()),
        )
        return int(cursor.lastrowid or 0)

    def signal_question_count(self, scope_id: str) -> int:
        """Number of questions queued for this scope."""
        row = self._db.fetchone(
            "SELECT COUNT(*) AS cnt FROM signal_questions WHERE scope_id = ?",
            (scope_id,),
        )
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    def open_ticket(self, user_id: str, scope_id: str, subject: str, details: str) -> TicketRecord:
        """Create a support ticket record."""
        subject = subject.strip() or "Support request"
        cursor = self._db.execute(
            "INSERT INTO tickets (user_id, scope_id, subject, details, status, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?)",
            (user_id, scope_id, subject[:150], details[:1900], time.time()),
        )
        return TicketRecord(
            ticket_id=int(cursor.lastrowid or 0),
            user_id=user_id,
            subject=subject,
            details=details,
        )

    def close_ticket(self, ticket_id: int, scope_id: str) -> bool:
        """Mark a ticket closed; returns True when a row was updated."""
        cursor = self._db.execute(
            "UPDATE tickets SET status = 'closed' WHERE id = ? AND scope_id = ?",
            (ticket_id, scope_id),
        )
        return cursor.rowcount > 0

    def ticket_for_channel(self, scope_id: str, subject_prefix: str) -> Optional[int]:
        """Find the newest open ticket whose subject starts with *prefix*."""
        row = self._db.fetchone(
            "SELECT id FROM tickets WHERE scope_id = ? AND status = 'open' "
            "AND subject LIKE ? ORDER BY id DESC LIMIT 1",
            (scope_id, f"{subject_prefix}%"),
        )
        return int(row["id"]) if row else None

    # ------------------------------------------------------------------
    def toggle_role(self, user_id: str, scope_id: str, platform: str, role_name: str) -> bool:
        """Toggle a stored role/tag; returns True when the role was granted."""
        existing = self._db.fetchone(
            "SELECT 1 FROM platform_roles "
            "WHERE user_id = ? AND scope_id = ? AND role_name = ? AND platform = ?",
            (user_id, scope_id, role_name, platform),
        )
        if existing:
            self._db.execute(
                "DELETE FROM platform_roles "
                "WHERE user_id = ? AND scope_id = ? AND role_name = ? AND platform = ?",
                (user_id, scope_id, role_name, platform),
            )
            return False
        self._db.execute(
            "INSERT OR IGNORE INTO platform_roles (user_id, scope_id, role_name, platform) "
            "VALUES (?, ?, ?, ?)",
            (user_id, scope_id, role_name, platform),
        )
        return True

    def roles_of(self, user_id: str, scope_id: str, platform: str) -> list[str]:
        """List the roles/tags a user holds in this scope on *platform*."""
        rows = self._db.fetchall(
            "SELECT role_name FROM platform_roles "
            "WHERE user_id = ? AND scope_id = ? AND platform = ? ORDER BY role_name",
            (user_id, scope_id, platform),
        )
        return [row["role_name"] for row in rows]
