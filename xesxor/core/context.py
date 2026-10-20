"""Platform-neutral command invocation context and adapter capabilities.

Handlers receive a :class:`CommandContext` and never touch Discord or
Telegram objects directly.  All platform side effects go through the
capability methods on :class:`PlatformAdapter`, which each driver
implements with its native idioms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from xesxor.core.models import (
    QuizQuestion,
    TicketRecord,
    UserRef,
)


@runtime_checkable
class PlatformAdapter(Protocol):
    """Capability surface a platform driver must provide to command handlers."""

    name: str

    async def send_text(
        self, ctx: "CommandContext", text: str, *, ephemeral: bool = False
    ) -> None:
        """Reply to the invoking command."""

    async def send_quiz(self, ctx: "CommandContext", quiz: QuizQuestion) -> None:
        """Render a quiz question with the platform's answer controls."""

    async def delete_messages(self, ctx: "CommandContext", amount: int) -> int:
        """Bulk-delete recent messages; returns how many were removed."""

    async def apply_member_action(
        self, ctx: "CommandContext", user: UserRef, action: str, hours: int = 0
    ) -> bool:
        """Apply a moderation action (``timeout`` / ``kick``); True on success."""

    async def apply_role(
        self, ctx: "CommandContext", user: UserRef, role_name: str, grant: bool
    ) -> bool:
        """Grant or revoke a role/tag for *user*; True on success."""

    async def deploy_verify_panel(self, ctx: "CommandContext") -> bool:
        """Post the verification panel in the current channel."""

    async def deploy_roles_panel(self, ctx: "CommandContext") -> bool:
        """Post the self-role / interest panel in the current channel."""

    async def deploy_ticket_panel(self, ctx: "CommandContext") -> bool:
        """Post the support-ticket panel in the current channel."""

    async def open_ticket(self, ctx: "CommandContext", ticket: TicketRecord) -> bool:
        """Create the platform-side representation of a support ticket."""

    async def publish_suggestion(self, ctx: "CommandContext", content: str, suggestion_id: int) -> bool:
        """Publish a community suggestion for voting/review."""

    async def publish_event(
        self, ctx: "CommandContext", title: str, time_str: str, description: str
    ) -> bool:
        """Publish a scheduled community event."""

    async def deliver_signal_question(self, ctx: "CommandContext", question: str) -> bool:
        """Deliver a queued weekly-signal question to the staff/news surface."""


@dataclass
class CommandContext:
    """Everything a command handler needs about the current invocation."""

    adapter: PlatformAdapter
    platform: str
    user: UserRef
    scope_id: str
    scope_name: str
    is_group: bool
    is_admin: bool
    channel_id: Optional[str] = None
    locale: Optional[str] = None
    raw: Any = None
    extras: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def display_name(self) -> str:
        """Human-readable name of the invoking user."""
        return self.user.display_name

    # Convenience delegates ------------------------------------------------
    async def reply(self, text: str, *, ephemeral: bool = False) -> None:
        """Send a reply to the invoking user."""
        await self.adapter.send_text(self, text, ephemeral=ephemeral)

    async def reply_error(self, text: str) -> None:
        """Send a user-facing error message."""
        await self.adapter.send_text(self, f"⚠️ {text}")

    async def send_quiz(self, quiz: QuizQuestion) -> None:
        """Render a quiz with answer controls."""
        await self.adapter.send_quiz(self, quiz)
