"""Plain domain models shared between services, commands and adapters.

These types are deliberately free of any Discord/Telegram imports so that
business logic stays portable across platforms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class UserRef:
    """Platform-neutral reference to a user.

    Attributes:
        id: Stable string identifier (Discord/Telegram user id).
        display_name: Best-effort human-readable name.
        raw: The original platform object, kept for adapter-side operations
            (adding Discord roles, kicking members, ...).  Never used by
            core services.
    """

    id: str
    display_name: str
    raw: Any = None

    def __int__(self) -> int:
        return int(self.id)


@dataclass
class UserProfile:
    """Economy / progression profile for one (user, scope) pair."""

    user_id: str
    scope_id: str
    coins: float = 100.0
    xp: int = 0
    level: int = 1
    messages: int = 0
    voice_minutes: int = 0
    reputation: int = 0
    streak: int = 0
    last_daily: float = 0.0
    joined_timestamp: float = 0.0


@dataclass(frozen=True)
class QuizQuestion:
    """A multiple-choice quiz ready to be rendered by any platform."""

    category: str
    question: str
    options: Tuple[str, ...]
    correct: str
    explanation: str
    fee: float
    reward: float
    used_today: int
    daily_limit: int

    @property
    def remaining_today(self) -> int:
        """Quizzes left for this user today."""
        return max(self.daily_limit - self.used_today, 0)

    def rendered_options(self) -> str:
        """Return options formatted as an ``A. ...`` bullet list."""
        labels = "ABCD"
        lines = []
        for index, text in enumerate(self.options):
            label = labels[index] if index < len(labels) else "?"
            lines.append(f"**{label}.** {text}")
        return "\n".join(lines)


@dataclass(frozen=True)
class QuizResult:
    """Outcome of answering a quiz question."""

    correct: bool
    correct_option: str
    reward: float
    explanation: str


@dataclass(frozen=True)
class WarnResult:
    """Outcome of issuing a moderation warning."""

    strikes: int
    action: Optional[str]  # None | "timeout" | "kick"
    action_hours: int = 0


@dataclass(frozen=True)
class LevelUpResult:
    """Returned when a message award pushed a user over a level threshold."""

    new_level: int


@dataclass(frozen=True)
class MessageReward:
    """Returned when a message-count milestone granted coins."""

    messages: int
    coins: float


@dataclass(frozen=True)
class Violation:
    """A detected automod rule violation."""

    rule: str
    action: str  # "delete" | "delete_notify"
    notice: str = ""


@dataclass(frozen=True)
class PurchaseResult:
    """Outcome of a shop purchase."""

    item: str
    price: float
    grants_role: bool


@dataclass(frozen=True)
class DailyResult:
    """Outcome of claiming the daily reward."""

    reward: float
    streak: int
    multiplier: float


@dataclass(frozen=True)
class WalletSnapshot:
    """Balance view used by the ``/balance`` command."""

    display_name: str
    coins: float
    level: int
    reputation: int
    messages: int
    voice_minutes: int


@dataclass(frozen=True)
class ShopItem:
    """A purchasable catalog entry (permanent roles, paid in earned Coins)."""

    name: str
    price: float
    grants_role: bool = True


@dataclass(frozen=True)
class TicketRecord:
    """Support ticket metadata."""

    ticket_id: int
    user_id: str
    subject: str
    details: str


__all__ = [
    "UserRef",
    "UserProfile",
    "QuizQuestion",
    "QuizResult",
    "WarnResult",
    "LevelUpResult",
    "MessageReward",
    "Violation",
    "PurchaseResult",
    "DailyResult",
    "WalletSnapshot",
    "ShopItem",
    "TicketRecord",
]
