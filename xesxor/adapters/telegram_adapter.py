"""Telegram driver (python-telegram-bot).

Compiles the shared command registry into ``/commands``, renders quizzes as
inline keyboards, supports both group chats and private messages, and maps
moderation/roles/tickets onto Telegram equivalents.  Business logic stays
in core services.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import datetime, timezone
from typing import Any, Optional

from telegram import (
    BotCommand,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from xesxor.commands.registry_build import ServiceContainer
from xesxor.config.settings import Settings
from xesxor.core.context import CommandContext
from xesxor.core.database import Database
from xesxor.core.errors import CommandError
from xesxor.core.models import QuizQuestion, TicketRecord, UserRef
from xesxor.core.registry import CommandRegistry, CommandParam, CommandSpec
from xesxor.core.services.automod import AutomodService
from xesxor.core.services.invites import InviteService
from xesxor.core.services.leveling import LevelingService

logger = logging.getLogger(__name__)

#: Interest tags offered by the Telegram self-roles panel.
ROLE_TAGS: tuple[str, ...] = (
    "Student", "Red Teamer", "Blue Teamer", "Bug Hunter", "CTF Player",
    "Malware Analyst", "Full-Stack Dev", "Frontend Dev", "Backend Dev", "DevOps Engineer",
)

QUIZ_CALLBACK_PREFIX = "quiz"
VERIFY_CALLBACK = "tgverify"
ROLE_CALLBACK_PREFIX = "tgrole"

MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)
FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
)


def _md(text: str) -> str:
    """Escape Markdown special characters for Telegram HTML-less mode."""
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


class TelegramAdapter:
    """Telegram driver implementing the platform capability contract."""

    name = "telegram"

    def __init__(
        self,
        settings: Settings,
        container: ServiceContainer,
        registry: CommandRegistry,
        database: Database,
        automod: AutomodService,
        leveling: LevelingService,
        invites: InviteService,
    ) -> None:
        self.settings = settings
        self.container = container
        self.registry = registry
        self.db = database
        self.automod = automod
        self.leveling = leveling
        self.invites = invites
        self.app = Application.builder().token(settings.telegram_token).build()
        self._news_task: Optional[asyncio.Task] = None
        self._register_handlers()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def _register_handlers(self) -> None:
        for spec in self.registry.for_platform(self.name):
            names = [spec.name.replace("-", "_")]
            names.extend(alias.replace("-", "_") for alias in spec.aliases)
            self.app.add_handler(CommandHandler(names, self._make_command(spec)))

        self.app.add_handler(CommandHandler(["verify"], self._on_verify_command))
        self.app.add_handler(CallbackQueryHandler(self._on_quiz_answer, pattern=f"^{QUIZ_CALLBACK_PREFIX}:"))
        self.app.add_handler(CallbackQueryHandler(self._on_verify_button, pattern=f"^{VERIFY_CALLBACK}$"))
        self.app.add_handler(CallbackQueryHandler(self._on_role_button, pattern=f"^{ROLE_CALLBACK_PREFIX}:"))
        self.app.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._on_new_members)
        )
        self.app.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
                self._on_group_message,
            )
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Connect, start polling and register the bot command menu."""
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
        )
        await self._set_command_menu()
        if self.settings.telegram_news_chat_ids:
            self._news_task = asyncio.create_task(self._news_loop())
        logger.info("Telegram adapter polling as %s", self.app.bot.username)

    async def stop(self) -> None:
        """Stop polling and shut the application down cleanly."""
        logger.info("Shutting down Telegram adapter...")
        if self._news_task:
            self._news_task.cancel()
            try:
                await self._news_task
            except asyncio.CancelledError:
                pass
        if self.app.updater and self.app.updater.running:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def _set_command_menu(self) -> None:
        commands = [
            BotCommand(spec.name.replace("-", "_"), spec.description[:64])
            for spec in self.registry.for_platform(self.name)
        ]
        try:
            await self.app.bot.set_my_commands(commands)
        except Exception as exc:
            logger.warning("Could not set Telegram command menu: %s", exc)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------
    def _make_command(self, spec: CommandSpec):
        async def _handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await self._dispatch(spec, update, context)

        return _handler

    async def _dispatch(
        self, spec: CommandSpec, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        try:
            ctx = await self._build_context(spec, update)
            if spec.admin_only and not ctx.is_admin:
                raise CommandError("This command requires administrator permissions.")
            if spec.guild_only and not ctx.is_group:
                raise CommandError("This command can only be used in a group chat.")
            args = self._parse_args(spec, update, message.text or "", message.chat_id)
            await spec.handler(ctx, args)
        except CommandError as exc:
            await message.reply_text(f"⚠️ {exc.message}")
        except Exception:
            logger.exception("Command /%s failed", spec.name)
            await message.reply_text(
                "⚠️ Something went wrong while running this command. Please try again."
            )

    async def _build_context(self, spec: CommandSpec, update: Update) -> CommandContext:
        message = update.effective_message
        user_obj = update.effective_user
        chat = update.effective_chat
        assert message is not None and user_obj is not None and chat is not None

        user = UserRef(
            id=str(user_obj.id),
            display_name=user_obj.full_name or user_obj.username or str(user_obj.id),
            raw=user_obj,
        )
        is_private = chat.type == "chat.Type.PRIVATE"
        is_admin = True
        if not is_private:
            try:
                member = await chat.get_member(user_obj.id)
                is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
            except Exception:
                is_admin = False
        if user_obj.id in self.settings.telegram_admin_ids:
            is_admin = True

        extras: dict[str, Any] = {}
        if spec.name == "ping":
            extras["latency_ms"] = None
        if spec.name == "serverinfo":
            extras["scope_info"] = (
                f"Type: {chat.type.value}\n"
                f"ID: `{chat.id}`\n"
                f"Members: {await self._safe_member_count(chat.id)}\n"
                f"Created: {chat.created_date.strftime('%Y-%m-%d') if chat.created_date else 'Unknown'}"
            )
        return CommandContext(
            adapter=self,
            platform=self.name,
            user=user,
            scope_id=str(chat.id),
            scope_name=chat.title or chat.full_name or "Direct Message",
            is_group=not is_private,
            is_admin=is_admin,
            channel_id=str(chat.id),
            raw=update,
            extras=extras,
        )

    async def _safe_member_count(self, chat_id: int) -> str:
        try:
            count = await self.app.bot.get_chat_member_count(chat_id)
            return f"{count:,}"
        except Exception:
            return "Unknown"

    def _parse_args(
        self, spec: CommandSpec, update: Update, text: str, chat_id: int
    ) -> dict[str, Any]:
        """Parse free-text command arguments into the spec's typed args."""
        message = update.effective_message
        assert message is not None
        parts = text.split(maxsplit=1)
        remainder = parts[1] if len(parts) > 1 else ""
        try:
            tokens = shlex.split(remainder)
        except ValueError:
            tokens = remainder.split()

        args: dict[str, Any] = {}
        params = list(spec.params)
        index = 0

        # A reply supplies the first "user" parameter for free.
        if params and params[0].type == "user" and message.reply_to_message:
            replied = message.reply_to_message.from_user
            if replied:
                args[params[0].name] = UserRef(
                    id=str(replied.id),
                    display_name=replied.full_name or replied.username or str(replied.id),
                    raw=replied,
                )
                params = params[1:]

        for position, param in enumerate(params):
            is_last = position == len(params) - 1
            if param.type == "user":
                if index >= len(tokens):
                    if not param.required:
                        continue
                    raise CommandError(
                        f"Missing argument `@{param.name}` — reply to a user's message "
                        f"or pass a username/id."
                    )
                args[param.name] = self._resolve_user_token(tokens[index], chat_id)
                index += 1
            elif param.type == "choice":
                if index >= len(tokens):
                    if not param.required:
                        continue
                    options = ", ".join(value for _, value in param.choices)
                    raise CommandError(f"Missing argument `{param.name}`. Options: {options}")
                choice = self._match_choice(param, tokens[index])
                if choice is None:
                    options = ", ".join(value for _, value in param.choices)
                    raise CommandError(f"Invalid `{param.name}`. Options: {options}")
                args[param.name] = choice
                index += 1
            elif param.type == "int":
                if index >= len(tokens):
                    if not param.required:
                        continue
                    raise CommandError(f"Missing argument `{param.name}` (number).")
                try:
                    args[param.name] = int(tokens[index].replace(",", ""))
                except ValueError:
                    raise CommandError(f"`{param.name}` must be a whole number.")
                index += 1
            elif param.type == "float":
                if index >= len(tokens):
                    if not param.required:
                        continue
                    raise CommandError(f"Missing argument `{param.name}` (number).")
                try:
                    args[param.name] = float(tokens[index].replace(",", ""))
                except ValueError:
                    raise CommandError(f"`{param.name}` must be a number.")
                index += 1
            else:  # str
                if is_last:
                    if index < len(tokens):
                        args[param.name] = " ".join(tokens[index:])
                        index = len(tokens)
                    elif not param.required:
                        continue
                    else:
                        raise CommandError(f"Missing argument `{param.name}`.")
                else:
                    if index >= len(tokens):
                        if not param.required:
                            continue
                        raise CommandError(f"Missing argument `{param.name}`.")
                    args[param.name] = tokens[index]
                    index += 1
        return args

    async def _resolve_user_token(self, token: str, chat_id: int) -> UserRef:
        """Resolve a numeric id or @username into a UserRef."""
        token = token.strip()
        raw_id: Optional[int] = None
        if token.lstrip("-").isdigit():
            raw_id = int(token)
        else:
            try:
                chat = await self.app.bot.get_chat(token)
                raw_id = chat.id
            except Exception:
                raise CommandError(
                    f"Could not resolve user `{token}`. Reply to their message instead."
                )
        try:
            member = await self.app.bot.get_chat_member(chat_id, raw_id)
            name = member.user.full_name or member.user.username or str(raw_id)
        except Exception:
            name = str(raw_id)
        return UserRef(id=str(raw_id), display_name=name, raw=None)

    @staticmethod
    def _match_choice(param: CommandParam, token: str) -> Optional[str]:
        lowered = token.lower()
        for label, value in param.choices:
            if lowered in (label.lower(), value.lower()):
                return value
        return None

    # ------------------------------------------------------------------
    # Callback queries
    # ------------------------------------------------------------------
    async def _on_quiz_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None or query.from_user is None:
            return
        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Invalid quiz payload.")
            return
        _, owner_id, choice = parts
        if str(query.from_user.id) != owner_id:
            await query.answer("This is not your quiz session!", show_alert=True)
            return
        scope_id = str(query.message.chat_id)
        try:
            result = await self.container.quiz.answer(owner_id, scope_id, choice)
        except CommandError as exc:
            await query.answer(exc.message, show_alert=True)
            return
        if result.correct:
            text = (
                f"✅ **Correct!** You won **{result.reward:.0f} Coins** "
                f"(10 fee refunded + 10 profit)."
            )
        else:
            text = (
                f"❌ **Incorrect!** The correct answer was **{result.correct_option}**. "
                "You lost 10 Coins."
            )
        if result.explanation:
            text += f"\n\n💡 **Why:** {result.explanation}"
        await query.edit_message_text(text)
        await query.answer()

    async def _on_verify_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/verify — text equivalent of the verify panel button."""
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        self.db.execute(
            "INSERT OR IGNORE INTO platform_roles (user_id, scope_id, role_name, platform) "
            "VALUES (?, ?, 'Verified', 'telegram')",
            (str(user.id), str(message.chat_id)),
        )
        if message.chat_id < 0:
            try:
                await context.bot.restrict_member(
                    message.chat_id, user.id, permissions=FULL_PERMISSIONS
                )
            except Exception:
                pass
        await message.reply_text("✅ Verified successfully! Welcome aboard!")

    async def _on_verify_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None or query.from_user is None:
            return
        user_id = str(query.from_user.id)
        chat_id = query.message.chat_id
        self.db.execute(
            "INSERT OR IGNORE INTO platform_roles (user_id, scope_id, role_name, platform) "
            "VALUES (?, ?, 'Verified', 'telegram')",
            (user_id, str(chat_id)),
        )
        if chat_id < 0:  # group chat: lift the mute if we applied one
            try:
                await self.app.bot.restrict_member(
                    chat_id, query.from_user.id, permissions=FULL_PERMISSIONS
                )
            except Exception:
                pass
        await query.answer("✅ Verified successfully! Welcome!")
        try:
            await query.edit_message_text("✅ You are now verified. Welcome aboard!")
        except Exception:
            pass

    async def _on_role_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None or query.from_user is None:
            return
        role_name = query.data.split(":", 1)[1]
        granted = self.container.community.toggle_role(
            str(query.from_user.id), str(query.message.chat_id), self.name, role_name
        )
        verb = "Added" if granted else "Removed"
        await query.answer(f"{verb} tag: {role_name}")
        try:
            tags = self.container.community.roles_of(
                str(query.from_user.id), str(query.message.chat_id), self.name
            )
            await query.edit_message_text(
                "🎨 **Your interest tags**\n" + ("• " + "\n• ".join(tags) if tags else "(none)")
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message pipeline (groups)
    # ------------------------------------------------------------------
    async def _on_new_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.chat.type == "chat.Type.PRIVATE":
            return
        scope_id = str(message.chat_id)
        for member in message.new_chat_members or []:
            if member.is_bot:
                continue
            self.invites.register_join(str(member.id), scope_id)
            if self.settings.telegram_require_verify:
                try:
                    await context.bot.restrict_member(
                        message.chat_id, member.id, permissions=MUTED_PERMISSIONS
                    )
                    await message.reply_text(
                        f"👋 Welcome {member.full_name}! You are muted until you press "
                        "the ✅ Verify button in the pinned message, or send /verify."
                    )
                except Exception as exc:
                    logger.warning("Could not restrict new member %s: %s", member.id, exc)

    async def _on_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or user.is_bot:
            return
        if message.chat.type == "chat.Type.PRIVATE":
            return

        member = None
        try:
            member = await message.chat.get_member(user.id)
        except Exception:
            pass
        is_admin = member is not None and member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )

        if not is_admin:
            violation = self.automod.check(
                str(user.id), str(message.chat_id), message.text or ""
            )
            if violation:
                try:
                    await message.delete()
                except Exception:
                    pass
                if violation.action == "delete_notify" and violation.notice:
                    try:
                        await message.reply_text(f"🚨 {violation.notice}")
                    except Exception:
                        pass
                return

        self.container.economy.ensure_user(str(user.id), str(message.chat_id))
        level_up, milestone = self.leveling.record_message(
            str(user.id), str(message.chat_id)
        )
        if milestone:
            try:
                await message.reply_text(
                    f"🪙 Milestone reached! **{milestone.messages:,} messages** sent — "
                    f"you earned **{milestone.coins:.0f} Coin**! 🎉"
                )
            except Exception:
                pass
        if level_up:
            try:
                await message.reply_text(
                    f"🚀 You leveled up to **Level {level_up.new_level}**! 🎉"
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # PlatformAdapter capabilities
    # ------------------------------------------------------------------
    async def send_text(self, ctx: CommandContext, text: str, *, ephemeral: bool = False) -> None:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return
        # Telegram has no true ephemeral messages; /ask previews are simply
        # replaced by the answer in the same chat.
        try:
            await message.reply_text(text, quote=False)
        except Exception as exc:
            logger.warning("Telegram reply failed: %s", exc)

    async def send_quiz(self, ctx: CommandContext, quiz: QuizQuestion) -> None:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{label}. {option[:40]}",
                        callback_data=f"{QUIZ_CALLBACK_PREFIX}:{ctx.user.id}:{label}",
                    )
                    for label, option in zip("ABCD", quiz.options)
                ]
            ]
        )
        text = (
            f"🧠 **XESXOR Quiz — {quiz.category}**\n\n"
            f"**{_md(quiz.question)}**\n\n"
            f"30s to answer • {quiz.used_today}/{quiz.daily_limit} used today "
            f"({quiz.remaining_today} left) • Fee: {quiz.fee:.0f} Coins → "
            f"Win: {quiz.reward:.0f} Coins"
        )
        await message.reply_text(text, reply_markup=keyboard, quote=False)

    async def delete_messages(self, ctx: CommandContext, amount: int) -> int:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None or not ctx.is_group:
            return 0
        chat_id = message.chat_id
        try:
            status = await self.app.bot.get_chat_member(chat_id, self.app.bot.id)
            can_delete = (
                status.status == ChatMemberStatus.ADMINISTRATOR
                and bool(status.can_delete_messages)
            )
        except Exception:
            can_delete = False
        deleted = 0
        try:
            history = await self.app.bot.get_chat_history(chat_id, limit=min(amount, 100))
        except Exception as exc:
            logger.warning("get_chat_history failed: %s", exc)
            return 0
        for old in history:
            if deleted >= amount:
                break
            owned_by_bot = old.from_user and old.from_user.id == self.app.bot.id
            if can_delete or owned_by_bot:
                try:
                    await self.app.bot.delete_messages(chat_id, [old.message_id])
                    deleted += 1
                except Exception:
                    pass
        return deleted

    async def apply_member_action(
        self, ctx: CommandContext, user: UserRef, action: str, hours: int = 0
    ) -> bool:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return False
        target_id = int(user.id)
        try:
            if action == "kick":
                await self.app.bot.ban_chat_member(message.chat_id, target_id)
                await self.app.bot.unban_chat_member(message.chat_id, target_id)
            elif action == "timeout":
                until = datetime.now(timezone.utc).timestamp() + (hours or 1) * 3600
                await self.app.bot.restrict_member(
                    message.chat_id, target_id, permissions=MUTED_PERMISSIONS, until_date=int(until)
                )
            else:
                return False
            return True
        except Exception as exc:
            logger.warning("Telegram member action %s failed: %s", action, exc)
            return False

    async def apply_role(
        self, ctx: CommandContext, user: UserRef, role_name: str, grant: bool
    ) -> bool:
        # Telegram has no server roles; purchased roles are stored as tags.
        if grant:
            self.db.execute(
                "INSERT OR IGNORE INTO platform_roles (user_id, scope_id, role_name, platform) "
                "VALUES (?, ?, ?, 'telegram')",
                (user.id, ctx.scope_id, role_name),
            )
        else:
            self.db.execute(
                "DELETE FROM platform_roles "
                "WHERE user_id = ? AND scope_id = ? AND role_name = ? AND platform = 'telegram'",
                (user.id, ctx.scope_id, role_name),
            )
        return True

    async def deploy_verify_panel(self, ctx: CommandContext) -> bool:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return False
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Verify to Access Server", callback_data=VERIFY_CALLBACK)]]
        )
        await message.reply_text(
            "🔒 **Verification**\nPress the button below to verify and unlock the chat.",
            reply_markup=keyboard,
        )
        return True

    async def deploy_roles_panel(self, ctx: CommandContext) -> bool:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return False
        rows: list[list[InlineKeyboardButton]] = []
        for index in range(0, len(ROLE_TAGS), 2):
            row = [
                InlineKeyboardButton(
                    tag, callback_data=f"{ROLE_CALLBACK_PREFIX}:{tag}"
                )
                for tag in ROLE_TAGS[index : index + 2]
            ]
            rows.append(row)
        await message.reply_text(
            "🎨 **Interest Tags**\nTap to add or remove a tag from your profile.",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return True

    async def deploy_ticket_panel(self, ctx: CommandContext) -> bool:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return False
        await message.reply_text(
            "🎫 **Support Tickets**\nOpen a ticket anytime with:\n"
            "`/ticket <subject> <details>`\n\nStaff will be notified immediately."
        )
        return True

    async def open_ticket(self, ctx: CommandContext, ticket: TicketRecord) -> bool:
        staff_chat = self.settings.telegram_staff_chat_id
        if not staff_chat:
            return False
        text = (
            f"🎫 **New Ticket #{ticket.ticket_id}**\n"
            f"From: {_md(ctx.display_name)} (`{ctx.user.id}`)\n"
            f"Chat: {_md(ctx.scope_name)} (`{ctx.scope_id}`)\n"
            f"Subject: {_md(ticket.subject)}\n\n{_md(ticket.details[:800])}"
        )
        try:
            await self.app.bot.send_message(staff_chat, text)
            return True
        except Exception as exc:
            logger.warning("Staff ticket relay failed: %s", exc)
            return False

    async def publish_suggestion(self, ctx: CommandContext, content: str, suggestion_id: int) -> bool:
        staff_chat = self.settings.telegram_staff_chat_id
        if staff_chat:
            try:
                await self.app.bot.send_message(
                    staff_chat,
                    f"💡 **Suggestion #{suggestion_id}** from {_md(ctx.display_name)} "
                    f"in {_md(ctx.scope_name)}:\n\n{_md(content[:1200])}",
                )
            except Exception as exc:
                logger.warning("Suggestion relay failed: %s", exc)
        return True

    async def publish_event(
        self, ctx: CommandContext, title: str, time_str: str, description: str
    ) -> bool:
        update: Update = ctx.raw
        message = update.effective_message
        if message is None:
            return False
        await message.reply_text(
            f"📅 **Community Event: {_md(title)}**\n\n{_md(description)}\n\n"
            f"⏰ **When:** {_md(time_str)}\nHosted by {_md(ctx.display_name)}"
        )
        return True

    async def deliver_signal_question(self, ctx: CommandContext, question: str) -> bool:
        staff_chat = self.settings.telegram_staff_chat_id
        if not staff_chat:
            return False
        count = self.container.community.signal_question_count(ctx.scope_id)
        try:
            await self.app.bot.send_message(
                staff_chat,
                f"📡 **Signal Question #{count}** from {_md(ctx.display_name)}:\n\n"
                f"{_md(question[:1200])}",
            )
            return True
        except Exception as exc:
            logger.warning("Signal relay failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Verify command (explicit, for muted members)
    # ------------------------------------------------------------------
    async def _news_loop(self) -> None:
        """Deliver AI briefings to the configured Telegram news chats."""
        interval = max(self.settings.news_interval_hours, 0.1) * 3600
        while True:
            await asyncio.sleep(interval)
            if not self.container.news.due():
                continue
            posted = False
            for chat_id in self.settings.telegram_news_chat_ids:
                try:
                    for kind in ("coding", "cyber"):
                        text = await self.container.news.generate(kind)
                        await self.app.bot.send_message(chat_id, text)
                    posted = True
                except Exception as exc:
                    logger.error("Telegram news delivery to %s failed: %s", chat_id, exc)
            if posted:
                self.container.news.mark_posted()
