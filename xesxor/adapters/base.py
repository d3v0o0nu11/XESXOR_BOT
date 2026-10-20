"""Shared adapter bootstrap: builds the service container once.

Both drivers receive the *same* service instances so economy, quizzes and
moderation state stay consistent no matter which platform a command
arrives on.
"""

from __future__ import annotations

from xesxor.commands.registry_build import ServiceContainer
from xesxor.config.settings import Settings
from xesxor.core.database import Database
from xesxor.core.services import (
    CommunityService,
    EconomyService,
    ModerationService,
    NewsService,
    QuizService,
)
from xesxor.core.services.ai import AIService
from xesxor.core.services.automod import AutomodService
from xesxor.core.services.invites import InviteService
from xesxor.core.services.leveling import LevelingService


def build_services(settings: Settings) -> tuple[ServiceContainer, Database, AutomodService, LevelingService, InviteService]:
    """Create the database, all services and the shared container.

    Args:
        settings: Loaded application settings.

    Returns:
        ``(container, database, automod, leveling, invites)`` — the
        container is what command handlers receive; the individual services
        are returned for adapter-side event pipelines (automod, XP, joins).
    """
    database = Database(settings.database_path)
    ai = AIService(settings.ai_api_key)
    economy = EconomyService(database)
    quiz = QuizService(database, ai, economy)
    moderation = ModerationService(database)
    community = CommunityService(database)
    news = NewsService(database, ai)
    automod = AutomodService()
    leveling = LevelingService(database)
    invites = InviteService(database)

    container = ServiceContainer(
        economy=economy,
        quiz=quiz,
        moderation=moderation,
        community=community,
        ai=ai,
        news=news,
    )
    return container, database, automod, leveling, invites
