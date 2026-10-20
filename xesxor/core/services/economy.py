"""Coins-only game economy.

The bot is completely free: Coins are *earned* through activity (chatting,
voice time, quizzes, invites, daily check-ins, community contributions)
and spent on permanent cosmetic roles.  There is no premium currency, no
paid tier and no external billing.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from xesxor.core.database import Database
from xesxor.core.errors import CommandError
from xesxor.core.models import (
    DailyResult,
    PurchaseResult,
    ShopItem,
    UserProfile,
    WalletSnapshot,
)

logger = logging.getLogger(__name__)

STARTING_COINS = 100.0

#: Catalog of permanent roles purchasable with earned Coins.
SHOP_ITEMS: tuple[ShopItem, ...] = (
    ShopItem(name="Mini Contributor", price=10_000),
    ShopItem(name="Contributor", price=50_000),
    ShopItem(name="Elite Contributor", price=100_000),
)

#: Staff-approved contribution payouts (Coins).
CONTRIBUTION_REWARDS: dict[str, float] = {
    "Approved Writeup": 1500,
    "Course Module": 3000,
    "Quiz Question": 25,
    "Bug Report": 300,
    "Feature Suggestion": 500,
    "Workshop Hosting": 200,
}

DAILY_BASE_REWARD = 10.0
DAILY_STREAK_STEP = 0.25
DAILY_MULTIPLIER_CAP = 2.5
THANKS_REWARD_COINS = 20.0


class EconomyService:
    """Wallet, daily rewards, gifting, shop and contribution payouts."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    def ensure_user(self, user_id: str, scope_id: str) -> UserProfile:
        """Create the user's profile on first touch and return it.

        Every new profile starts with :data:`STARTING_COINS`.
        """
        self._db.execute(
            "INSERT INTO users (user_id, scope_id, coins, joined_timestamp) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, scope_id) DO NOTHING",
            (user_id, scope_id, STARTING_COINS, time.time()),
        )
        profile = self.get_profile(user_id, scope_id)
        if profile is None:  # pragma: no cover - defensive
            raise CommandError("Could not create your economy profile.")
        return profile

    def get_profile(self, user_id: str, scope_id: str) -> Optional[UserProfile]:
        """Return the stored profile or ``None`` when it does not exist."""
        row = self._db.fetchone(
            "SELECT * FROM users WHERE user_id = ? AND scope_id = ?",
            (user_id, scope_id),
        )
        return self._row_to_profile(row) if row else None

    @staticmethod
    def _row_to_profile(row) -> UserProfile:
        return UserProfile(
            user_id=row["user_id"],
            scope_id=row["scope_id"],
            coins=row["coins"],
            xp=row["xp"],
            level=row["level"],
            messages=row["messages"],
            voice_minutes=row["voice_minutes"],
            reputation=row["reputation"],
            streak=row["streak"],
            last_daily=row["last_daily"],
            joined_timestamp=row["joined_timestamp"],
        )

    # ------------------------------------------------------------------
    def wallet(self, user_id: str, scope_id: str, display_name: str) -> WalletSnapshot:
        """Return a balance snapshot for display."""
        profile = self.ensure_user(user_id, scope_id)
        return WalletSnapshot(
            display_name=display_name,
            coins=profile.coins,
            level=profile.level,
            reputation=profile.reputation,
            messages=profile.messages,
            voice_minutes=profile.voice_minutes,
        )

    def add_coins(self, user_id: str, scope_id: str, amount: float) -> None:
        """Grant Coins to a user, provisioning the profile if needed."""
        self.ensure_user(user_id, scope_id)
        self._db.execute(
            "UPDATE users SET coins = coins + ? WHERE user_id = ? AND scope_id = ?",
            (amount, user_id, scope_id),
        )

    # ------------------------------------------------------------------
    def claim_daily(self, user_id: str, scope_id: str) -> DailyResult:
        """Claim the daily reward with a consecutive-day multiplier.

        Raises:
            CommandError: when the daily was already claimed in the last 24h.
        """
        self.ensure_user(user_id, scope_id)
        now = time.time()
        row = self._db.fetchone(
            "SELECT last_daily, streak FROM users WHERE user_id = ? AND scope_id = ?",
            (user_id, scope_id),
        )
        last, streak = (row["last_daily"], row["streak"]) if row else (0.0, 0)

        if now - last < 86_400:
            remaining = int(86_400 - (now - last))
            hours, minutes = divmod(remaining // 60, 60)
            raise CommandError(f"Daily check-in already claimed! Come back in **{hours}h {minutes}m**.")

        streak = streak + 1 if (0 < now - last < 172_800) else 1
        multiplier = min(1.0 + streak * DAILY_STREAK_STEP, DAILY_MULTIPLIER_CAP)
        reward = DAILY_BASE_REWARD * multiplier

        self._db.execute(
            "UPDATE users SET coins = coins + ?, streak = ?, last_daily = ? "
            "WHERE user_id = ? AND scope_id = ?",
            (reward, streak, now, user_id, scope_id),
        )
        return DailyResult(reward=reward, streak=streak, multiplier=multiplier)

    # ------------------------------------------------------------------
    def gift(self, sender_id: str, scope_id: str, target_id: str, amount: float) -> None:
        """Transfer Coins between two users inside one scope.

        Raises:
            CommandError: on self-gifts, invalid amounts or insufficient funds.
        """
        if sender_id == target_id:
            raise CommandError("You cannot gift yourself!")
        if amount <= 0:
            raise CommandError("Gift amount must be positive.")

        self.ensure_user(sender_id, scope_id)
        self.ensure_user(target_id, scope_id)

        with self._db.transaction() as cursor:
            cursor.execute(
                "SELECT coins FROM users WHERE user_id = ? AND scope_id = ?",
                (sender_id, scope_id),
            )
            row = cursor.fetchone()
            balance = row["coins"] if row else 0.0
            if balance < amount:
                raise CommandError(
                    f"Insufficient Coins. You have {balance:,.2f} Coins."
                )
            cursor.execute(
                "UPDATE users SET coins = coins - ? WHERE user_id = ? AND scope_id = ?",
                (amount, sender_id, scope_id),
            )
            cursor.execute(
                "INSERT INTO users (user_id, scope_id, coins) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, scope_id) DO UPDATE SET coins = coins + ?",
                (target_id, scope_id, amount, amount),
            )

    # ------------------------------------------------------------------
    @staticmethod
    def catalog() -> tuple[ShopItem, ...]:
        """Return the shop catalog."""
        return SHOP_ITEMS

    @staticmethod
    def get_item(name: str) -> Optional[ShopItem]:
        """Look up a catalog item by (case-insensitive) name."""
        lowered = name.strip().lower()
        for item in SHOP_ITEMS:
            if item.name.lower() == lowered:
                return item
        return None

    def buy(self, user_id: str, scope_id: str, item_name: str) -> PurchaseResult:
        """Purchase a permanent role with earned Coins.

        Raises:
            CommandError: unknown item or insufficient Coins.
        """
        item = self.get_item(item_name)
        if item is None:
            raise CommandError(
                f"Unknown shop item `{item_name}`. See /shop for the catalog."
            )

        self.ensure_user(user_id, scope_id)
        with self._db.transaction() as cursor:
            cursor.execute(
                "SELECT coins FROM users WHERE user_id = ? AND scope_id = ?",
                (user_id, scope_id),
            )
            row = cursor.fetchone()
            balance = row["coins"] if row else 0.0
            if balance < item.price:
                raise CommandError(
                    f"Insufficient Coins. **{item.name}** costs {item.price:,.0f} Coins "
                    f"(you have {balance:,.2f})."
                )
            cursor.execute(
                "UPDATE users SET coins = coins - ? WHERE user_id = ? AND scope_id = ?",
                (item.price, user_id, scope_id),
            )
            cursor.execute(
                "INSERT INTO inventory (user_id, scope_id, item_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, scope_id, item.name, time.time()),
            )
        logger.info("Purchase: user=%s scope=%s item=%s", user_id, scope_id, item.name)
        return PurchaseResult(item=item.name, price=item.price, grants_role=item.grants_role)

    def inventory(self, user_id: str, scope_id: str) -> list[str]:
        """Return the item names a user owns in this scope."""
        rows = self._db.fetchall(
            "SELECT item_name FROM inventory WHERE user_id = ? AND scope_id = ? ORDER BY id",
            (user_id, scope_id),
        )
        return [row["item_name"] for row in rows]

    # ------------------------------------------------------------------
    def thanks(self, sender_id: str, scope_id: str, target_id: str) -> None:
        """Give +1 reputation and +20 Coins to a helpful member.

        Raises:
            CommandError: when thanking yourself.
        """
        if sender_id == target_id:
            raise CommandError("You cannot thank yourself!")
        self.ensure_user(target_id, scope_id)
        self._db.execute(
            "INSERT INTO users (user_id, scope_id, reputation, coins) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, scope_id) DO UPDATE SET "
            "reputation = reputation + 1, coins = coins + ?",
            (target_id, scope_id, THANKS_REWARD_COINS, THANKS_REWARD_COINS),
        )

    def award_contribution(self, scope_id: str, target_id: str, activity: str) -> float:
        """Award staff-approved contribution Coins.

        Returns:
            The number of Coins granted.

        Raises:
            CommandError: for unknown activity types.
        """
        coins = CONTRIBUTION_REWARDS.get(activity)
        if coins is None:
            raise CommandError(f"Unknown contribution type: `{activity}`.")
        self.ensure_user(target_id, scope_id)
        self._db.execute(
            "INSERT INTO users (user_id, scope_id, coins) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, scope_id) DO UPDATE SET coins = coins + ?",
            (target_id, scope_id, coins, coins),
        )
        return coins
