"""XESXOR Bot — multi-platform (Discord + Telegram) community bot.

Packages
--------
config      Environment-driven settings (never logs secrets).
core        Platform-agnostic business logic: services, database, command registry.
commands    Unified command definitions shared by every platform adapter.
adapters    Platform drivers: Discord (discord.py) and Telegram (python-telegram-bot).
utils       Logging setup and small helpers.
"""

__version__ = "2.0.0"
