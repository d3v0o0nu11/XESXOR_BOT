"""Environment-driven application settings.

All secrets are read from environment variables (optionally via a ``.env``
file loaded with python-dotenv).  Secrets are never logged and never have
insecure fallback defaults: if a required token is missing the feature is
disabled (or the process refuses to start when *no* platform is configured).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv


def _get_bool(name: str, default: bool = False) -> bool:
    """Parse an environment variable as a boolean (``1/true/yes/on``)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    """Parse an environment variable as an int, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _get_int_tuple(name: str) -> Tuple[int, ...]:
    """Parse a comma-separated list of integers from the environment."""
    raw = os.getenv(name, "")
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            continue
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration assembled from the environment."""

    # --- Platform credentials (secrets) ---------------------------------
    discord_token: Optional[str] = None
    telegram_token: Optional[str] = None
    ai_api_key: Optional[str] = None

    # --- Storage / runtime ----------------------------------------------
    database_path: str = "bot_database.db"
    log_level: str = "INFO"
    port: int = 10000
    keep_alive_enabled: bool = True
    # Interface the keep-alive web server binds to.  Defaults to localhost so
    # the status endpoint is never reachable from the internet; set to
    # 0.0.0.0 only on free-tier hosts that need external pings.
    keep_alive_host: str = "127.0.0.1"

    # --- Discord behaviour ----------------------------------------------
    # When True the bot bootstraps its full server layout (roles, channels,
    # permission lockdown) in every guild it joins.  Keep False for public
    # deployments so joining a foreign server never rewrites its permissions.
    discord_auto_setup: bool = False

    # --- Telegram behaviour ---------------------------------------------
    telegram_admin_ids: Tuple[int, ...] = field(default_factory=tuple)
    telegram_news_chat_ids: Tuple[int, ...] = field(default_factory=tuple)
    telegram_staff_chat_id: Optional[int] = None
    # Mute newly joined group members until they press /verify.
    telegram_require_verify: bool = False

    # --- News schedule ---------------------------------------------------
    news_interval_hours: float = 8.0

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, env_file: Optional[str] = None) -> "Settings":
        """Load settings from the process environment (and optional ``.env``).

        Args:
            env_file: Explicit path to an env file.  When ``None``,
                python-dotenv's default discovery is used (``./.env``).

        Returns:
            A fully populated :class:`Settings` instance.
        """
        if env_file is not None:
            load_dotenv(env_file)
        else:
            load_dotenv()

        return cls(
            discord_token=os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or None,
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            ai_api_key=os.getenv("AI_API_KEY") or None,
            database_path=os.getenv("DATABASE_PATH", "bot_database.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            port=_get_int("PORT", 10000),
            keep_alive_enabled=_get_bool("KEEP_ALIVE_ENABLED", True),
            keep_alive_host=os.getenv("KEEP_ALIVE_HOST", "127.0.0.1").strip() or "127.0.0.1",
            discord_auto_setup=_get_bool("DISCORD_AUTO_SETUP", False),
            telegram_admin_ids=_get_int_tuple("TELEGRAM_ADMIN_IDS"),
            telegram_news_chat_ids=_get_int_tuple("TELEGRAM_NEWS_CHAT_IDS"),
            telegram_staff_chat_id=(
                _get_int("TELEGRAM_STAFF_CHAT_ID", 0) or None
            ),
            telegram_require_verify=_get_bool("TELEGRAM_REQUIRE_VERIFY", False),
            news_interval_hours=float(os.getenv("NEWS_INTERVAL_HOURS", "8") or 8),
        )

    # ------------------------------------------------------------------
    @property
    def discord_enabled(self) -> bool:
        """Whether the Discord driver should be started."""
        return bool(self.discord_token)

    @property
    def telegram_enabled(self) -> bool:
        """Whether the Telegram driver should be started."""
        return bool(self.telegram_token)

    def ensure_something_to_run(self) -> None:
        """Validate that at least one platform token is configured.

        Raises:
            RuntimeError: when neither Discord nor Telegram is configured.
        """
        if not self.discord_enabled and not self.telegram_enabled:
            raise RuntimeError(
                "No platform configured. Set DISCORD_BOT_TOKEN and/or "
                "TELEGRAM_BOT_TOKEN in your environment (see .env.example)."
            )

    def create_database(self) -> None:
        """Ensure the parent directory of the SQLite database exists."""
        parent = Path(self.database_path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)

    # Secret-safe debug representation.
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            "Settings("
            f"discord={'set' if self.discord_token else 'unset'}, "
            f"telegram={'set' if self.telegram_token else 'unset'}, "
            f"ai={'set' if self.ai_api_key else 'unset'}, "
            f"db={self.database_path!r}, log={self.log_level!r}, "
            f"auto_setup={self.discord_auto_setup})"
        )

    __str__ = __repr__
