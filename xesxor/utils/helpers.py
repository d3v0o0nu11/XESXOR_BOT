"""Small pure helpers shared across layers."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Optional


def utc_now_ts() -> float:
    """Return the current Unix timestamp (UTC)."""
    return time.time()


def utc_day_start_ts() -> float:
    """Return the Unix timestamp of today's 00:00 UTC."""
    now_utc = datetime.now(timezone.utc)
    return datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc).timestamp()


def question_hash(text: str) -> str:
    """Deterministic content hash used to de-duplicate quiz questions."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def format_countdown(seconds_remaining: int) -> str:
    """Render a human-readable ``Xh Ym`` countdown.

    Args:
        seconds_remaining: Number of seconds left (clamped at 0).

    Returns:
        A string such as ``"3h 12m"`` or ``"45m"``.
    """
    remaining = max(int(seconds_remaining), 0)
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def clamp(text: str, limit: int) -> str:
    """Truncate *text* to *limit* characters, appending an ellipsis marker."""
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def is_high_caps(text: str, min_length: int = 20, threshold: float = 0.75) -> bool:
    """Return True when *text* is mostly uppercase (caps-spam heuristic)."""
    if len(text) <= min_length:
        return False
    uppercase = sum(1 for char in text if char.isupper())
    return uppercase / len(text) > threshold


def parse_positive_float(raw: Optional[str]) -> Optional[float]:
    """Parse a positive float from user input, returning ``None`` on failure."""
    if raw is None:
        return None
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
