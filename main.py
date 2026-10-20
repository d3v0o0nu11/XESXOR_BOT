#!/usr/bin/env python3
"""XESXOR Bot entrypoint.

Boots every configured platform driver (Discord and/or Telegram) on one
asyncio loop, wires shared services, and performs a graceful shutdown on
SIGINT/SIGTERM so neither connection is dropped mid-operation.

Usage:
    python main.py
"""

from __future__ import annotations

import asyncio
import logging
import signal

from xesxor import __version__
from xesxor.adapters.base import build_services
from xesxor.commands.registry_build import build_registry
from xesxor.config.settings import Settings
from xesxor.utils.logging import setup_logging

logger = logging.getLogger("xesxor")


async def _start_adapter(adapter) -> None:
    """Start one platform adapter.

    discord.py's ``Client.start`` needs the token argument, so the Discord
    adapter exposes ``connect_with_token``; Telegram exposes ``start``.
    """
    if hasattr(adapter, "connect_with_token"):
        await adapter.connect_with_token()
    else:
        await adapter.start()


async def run() -> None:
    """Start all configured adapters and block until a shutdown signal."""
    settings = Settings.load()
    settings.ensure_something_to_run()
    settings.create_database()
    setup_logging(settings.log_level)

    logger.info("XESXOR Bot v%s starting...", __version__)
    logger.info("Configuration: %s", settings)

    container, database, automod, leveling, invites = build_services(settings)
    registry = build_registry(container)
    logger.info("Registered %d cross-platform commands.", len(registry))

    adapters: list = []
    discord_adapter = None
    if settings.discord_enabled:
        from xesxor.adapters.discord_adapter import DiscordAdapter

        discord_adapter = DiscordAdapter(
            settings, container, registry, database, automod, leveling, invites
        )
        adapters.append(discord_adapter)

    telegram_adapter = None
    if settings.telegram_enabled:
        from xesxor.adapters.telegram_adapter import TelegramAdapter

        telegram_adapter = TelegramAdapter(
            settings, container, registry, database, automod, leveling, invites
        )
        adapters.append(telegram_adapter)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass

    # Start every adapter concurrently; one platform failing must not
    # prevent the other from running.
    results = await asyncio.gather(
        *(_start_adapter(adapter) for adapter in adapters),
        return_exceptions=True,
    )
    for adapter, result in zip(adapters, results):
        if isinstance(result, BaseException):
            logger.error(
                "Adapter %s failed to start: %s", getattr(adapter, "name", "?"), result
            )
    started = [
        adapter for adapter, result in zip(adapters, results)
        if not isinstance(result, BaseException)
    ]
    if not started:
        database.close()
        raise SystemExit("No platform adapter could be started.")

    logger.info(
        "Running with platforms: %s — press Ctrl+C to stop.",
        ", ".join(adapter.name for adapter in started),
    )

    try:
        await stop_event.wait()
        logger.info("Shutdown signal received.")
    finally:
        # Stop in reverse start order.
        for adapter in reversed(started):
            try:
                if hasattr(adapter, "stop"):
                    await adapter.stop()
                else:
                    await adapter.close()
            except Exception:
                logger.exception("Error stopping adapter %s", getattr(adapter, "name", "?"))
        database.close()
        logger.info("Shutdown complete.")


def main() -> None:
    """Console entrypoint."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
