"""Platform-agnostic core: domain models, services, command registry."""

from xesxor.core.errors import CommandError, ServiceError

__all__ = ["CommandError", "ServiceError"]
