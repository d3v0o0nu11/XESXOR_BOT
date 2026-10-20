"""Domain exceptions.

Services and command handlers raise :class:`CommandError` for *expected*
user-facing failures (insufficient funds, missing permissions...).  Adapters
catch it and render the message with the platform's own formatting.  Any
other exception is treated as a bug, logged with a traceback and answered
with a generic apology.
"""

from __future__ import annotations


class XesxorError(Exception):
    """Base class for all application-level errors."""


class ServiceError(XesxorError):
    """A service could not complete an operation for an expected reason."""


class CommandError(XesxorError):
    """A user-facing command failure whose message may be shown directly.

    Attributes:
        message: Human-readable explanation safe to show to the user.
        hint: Optional extra guidance appended on some platforms.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
