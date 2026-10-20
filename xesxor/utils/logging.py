"""Central logging configuration with credential redaction.

Every log record passes through :class:`SecretRedactionFilter`, which masks
strings that look like platform credentials (Discord/Telegram/GitHub/AI
tokens) so that secrets can never end up in log files or console output,
even if an exception message embeds one.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Iterable

# Patterns deliberately over-broad: better a redacted fragment than a leak.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Discord bot tokens: three base64url segments separated by dots.
    re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{25,120}"),
    # Telegram bot tokens: numeric id, colon, secret.
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,60}\b"),
    # GitHub PATs (classic and fine-grained).
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,255}\b"),
    # Google AI / Gemini API keys.
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,60}\b"),
    re.compile(r"\bAQ\.[A-Za-z0-9_-]{20,120}\b"),
    # Authorization / API-key header style secrets.
    re.compile(
        r"(?i)(?P<prefix>(?:authorization|api-key|apikey)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?)[^\s\"']+"
    ),
    # Bare bearer tokens and JWTs.
    re.compile(r"(?i)(?P<prefix>\bbearer\s+)[^\s\"']+"),
    re.compile(r"(?P<prefix>\b)eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"),
    # key=... / token=... style secrets (query-string or bare assignments).
    # The prefix guard is a lookbehind rather than \b so that compound names
    # like ``ai_api_key=...`` (preceded by an underscore) still match.
    re.compile(
        r"(?i)(?P<prefix>(?:[?&]|(?<![A-Za-z0-9]))(?:key|token|secret|api_key|apikey|password|passwd)=)[^&\s\"']+"
    ),
)

_REDACTED = "***REDACTED***"


def _mask(match: "re.Match[str]") -> str:
    """Replace the secret part of a match, keeping any labelled prefix."""
    prefix = match.groupdict().get("prefix") or ""
    return prefix + _REDACTED


def redact(text: str) -> str:
    """Return *text* with every credential-looking token masked.

    Args:
        text: Arbitrary text, typically a formatted log message.

    Returns:
        The text with secrets replaced by ``***REDACTED***``.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_mask, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Logging filter that redacts credentials from rendered records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure root logging for the whole process.

    Args:
        level: Minimum severity to emit (``DEBUG``, ``INFO``, ...).
        log_file: Optional path to also append records to.

    Raises:
        ValueError: if *level* is not a valid logging level name.
    """
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level!r}")

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Drop any handlers installed by libraries or previous calls.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    redaction = SecretRedactionFilter()
    for handler in root.handlers:
        handler.addFilter(redaction)

    # Quiet noisy third-party loggers a bit.
    for name in ("telegram", "httpx", "aiohttp.access", "discord.gateway"):
        logging.getLogger(name).setLevel(max(numeric_level, logging.INFO))


def add_redaction_to(logger: logging.Logger, handlers: Iterable[logging.Handler]) -> None:
    """Attach the redaction filter to arbitrary handlers (helper for adapters)."""
    redaction = SecretRedactionFilter()
    for handler in handlers:
        handler.addFilter(redaction)
