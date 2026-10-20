"""Platform-agnostic business services.

Each service owns one domain (economy, quizzes, moderation, ...) and talks
only to :class:`xesxor.core.database.Database`.  Services never import
platform SDKs; adapters render the dataclasses returned here.
"""

from xesxor.core.services.ai import AIService
from xesxor.core.services.automod import AutomodService
from xesxor.core.services.community import CommunityService
from xesxor.core.services.economy import EconomyService
from xesxor.core.services.invites import InviteService
from xesxor.core.services.leveling import LevelingService
from xesxor.core.services.moderation import ModerationService
from xesxor.core.services.news import NewsService
from xesxor.core.services.quiz import QuizService

__all__ = [
    "AIService",
    "AutomodService",
    "CommunityService",
    "EconomyService",
    "InviteService",
    "LevelingService",
    "ModerationService",
    "NewsService",
    "QuizService",
]
