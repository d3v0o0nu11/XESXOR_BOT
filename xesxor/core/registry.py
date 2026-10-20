"""Unified command registry.

Every feature is declared exactly once as a :class:`CommandSpec`.  Platform
adapters compile the registry into their native command surfaces — Discord
slash commands and Telegram ``/commands`` — so feature logic is never
duplicated between drivers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional

from xesxor.core.context import CommandContext

#: Handler signature shared by every command on every platform.
Handler = Callable[[CommandContext, dict[str, Any]], Awaitable[None]]

#: Supported argument kinds understood by the adapters.
PARAM_TYPES = frozenset({"str", "int", "float", "user", "choice"})


@dataclass(frozen=True)
class CommandParam:
    """Declarative description of one command argument.

    Attributes:
        name: Argument identifier; also the key in the handler's ``args`` dict.
        description: Help text shown in slash-command UIs.
        type: One of ``str``, ``int``, ``float``, ``user``, ``choice``.
        required: Whether the argument must be supplied.
        choices: For ``choice`` params, tuples of ``(label, value)``.
    """

    name: str
    description: str
    type: str = "str"
    required: bool = True
    choices: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"Unsupported param type: {self.type!r}")
        if self.type == "choice" and not self.choices:
            raise ValueError(f"Choice param {self.name!r} needs choices")


@dataclass(frozen=True)
class CommandSpec:
    """Declarative definition of one cross-platform command."""

    name: str
    description: str
    handler: Handler
    params: tuple[CommandParam, ...] = ()
    usage: str = ""
    admin_only: bool = False
    guild_only: bool = False
    platforms: tuple[str, ...] = ("discord", "telegram")
    aliases: tuple[str, ...] = ()

    def supports(self, platform: str) -> bool:
        """Return True when this command should exist on *platform*."""
        return platform in self.platforms


class CommandRegistry:
    """Collection of :class:`CommandSpec` instances keyed by command name."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    # ------------------------------------------------------------------
    def register(self, spec: CommandSpec) -> CommandSpec:
        """Add *spec* to the registry.

        Args:
            spec: The command definition.

        Returns:
            The registered spec (allows inline registration).

        Raises:
            ValueError: when a command with the same name already exists.
        """
        if spec.name in self._commands:
            raise ValueError(f"Duplicate command registration: {spec.name}")
        self._commands[spec.name] = spec
        return spec

    def get(self, name: str) -> Optional[CommandSpec]:
        """Look up a command by name or alias."""
        spec = self._commands.get(name)
        if spec is not None:
            return spec
        for candidate in self._commands.values():
            if name in candidate.aliases:
                return candidate
        return None

    def all(self) -> list[CommandSpec]:
        """Return every registered command."""
        return list(self._commands.values())

    def for_platform(self, platform: str) -> list[CommandSpec]:
        """Return commands available on *platform*, in registration order."""
        return [spec for spec in self._commands.values() if spec.supports(platform)]

    def __len__(self) -> int:
        return len(self._commands)

    def __iter__(self) -> Iterable[CommandSpec]:
        return iter(self._commands.values())
