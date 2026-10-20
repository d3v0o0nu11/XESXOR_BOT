"""Discord driver (discord.py).

Owns everything Discord-specific: slash-command compilation from the
shared registry, persistent UI views, server events, the news loop and the
optional keep-alive web server.  Business logic lives in core services —
this module only adapts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks

from xesxor.commands.registry_build import ServiceContainer
from xesxor.config.settings import Settings
from xesxor.core.context import CommandContext
from xesxor.core.database import Database
from xesxor.core.errors import CommandError
from xesxor.core.models import QuizQuestion, TicketRecord, UserRef
from xesxor.core.registry import CommandRegistry, CommandSpec
from xesxor.core.services.automod import AutomodService
from xesxor.core.services.invites import InviteService
from xesxor.core.services.leveling import LevelingService

logger = logging.getLogger(__name__)

ROLE_PANEL: tuple[str, ...] = (
    "Student", "Red Teamer", "Blue Teamer", "Bug Hunter", "CTF Player",
    "Malware Analyst", "Full-Stack Dev", "Frontend Dev", "Backend Dev", "DevOps Engineer",
)

STAFF_ROLE_NAMES: frozenset[str] = frozenset(
    {"server admin", "moderator", "team member", "xesxor developer", "admin", "staff"}
)

RETRY_DELAYS: tuple[int, ...] = (2, 5, 10, 20)


# ======================================================================
# Persistent views
# ======================================================================

class VerificationView(discord.ui.View):
    """One-click verification panel granting the Member role."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Verify to Access Server",
        style=discord.ButtonStyle.green,
        custom_id="verify_btn_v2",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            guild = interaction.guild
            if guild is None:
                return
            role = discord.utils.get(guild.roles, name="Member")
            if role is None:
                role = await guild.create_role(name="Member", color=discord.Color.green())
            if role in interaction.user.roles:  # type: ignore[union-attr]
                await interaction.response.send_message("You are already verified!", ephemeral=True)
            else:
                await interaction.user.add_roles(role)  # type: ignore[union-attr]
                await interaction.response.send_message(
                    "🎉 Verified successfully! Welcome to the server!", ephemeral=True
                )
        except Exception as exc:
            logger.error("Verify button error: %s", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ Verification failed, please contact staff.", ephemeral=True
                )


class RoleToggleView(discord.ui.View):
    """Self-role panel toggling one role per button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for index, role_name in enumerate(ROLE_PANEL):
            style = (
                discord.ButtonStyle.danger if "Team" in role_name or "Malware" in role_name
                else discord.ButtonStyle.primary if "Dev" in role_name or "Ops" in role_name
                else discord.ButtonStyle.secondary
            )
            self.add_item(RoleToggleButton(role_name, style, row=index // 5))


class RoleToggleButton(discord.ui.Button):
    """A single self-role toggle button."""

    def __init__(self, role_name: str, style: discord.ButtonStyle, row: int) -> None:
        super().__init__(label=role_name, style=style, custom_id=f"role_v2_{role_name}", row=row)
        self.role_name = role_name

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            if guild is None:
                return
            member = interaction.user
            role = discord.utils.get(guild.roles, name=self.role_name)
            if role is None:
                role = await guild.create_role(name=self.role_name)
            if role in member.roles:  # type: ignore[union-attr]
                await member.remove_roles(role)  # type: ignore[union-attr]
                await interaction.response.send_message(
                    f"Removed role: {self.role_name}", ephemeral=True
                )
            else:
                await member.add_roles(role)  # type: ignore[union-attr]
                await interaction.response.send_message(
                    f"Added role: {self.role_name}", ephemeral=True
                )
        except Exception as exc:
            logger.error("Role toggle error: %s", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ Could not update roles, please contact staff.", ephemeral=True
                )


class TicketCreateButton(discord.ui.Button):
    """Button that opens the ticket modal."""

    def __init__(self) -> None:
        super().__init__(
            label="🎫 Create Support Ticket",
            style=discord.ButtonStyle.blurple,
            custom_id="create_ticket_v2",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketModal())


class TicketSetupView(discord.ui.View):
    """Panel that opens the ticket-creation modal."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketCreateButton())


class TicketModal(discord.ui.Modal, title="Create Support Ticket"):
    """Modal collecting ticket subject and details."""

    subject = discord.ui.TextInput(
        label="Issue / Subject", placeholder="Briefly describe your issue...", required=True
    )
    details = discord.ui.TextInput(
        label="Detailed Explanation",
        style=discord.TextStyle.paragraph,
        placeholder="Provide logs or details...",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            if guild is None:
                return
            category = discord.utils.get(guild.categories, name="Support Tickets")
            if category is None:
                category = await guild.create_category("Support Tickets")
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            ticket_channel = await guild.create_text_channel(
                f"ticket-{interaction.user.name}", category=category, overwrites=overwrites
            )
            embed = discord.Embed(
                title=f"🎫 Support Ticket: {self.subject.value}",
                description=self.details.value[:4000],
                color=discord.Color.blue(),
            )
            embed.set_author(
                name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
            )
            await ticket_channel.send(
                content=f"{interaction.user.mention} Welcome! Staff will be with you shortly.",
                embed=embed,
                view=TicketCloseView(),
            )
            await interaction.response.send_message(
                f"Ticket created: {ticket_channel.mention}", ephemeral=True
            )
        except Exception as exc:
            logger.error("Ticket submit error: %s", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ Could not create ticket, please contact staff.", ephemeral=True
                )


class TicketCloseView(discord.ui.View):
    """Close button attached to new ticket channels."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_v2"
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("Ticket channel delete failed: %s", exc)


class QuizView(discord.ui.View):
    """A/B/C/D answer buttons for one quiz session."""

    def __init__(self, quiz_service: Any, user_id: str, scope_id: str, quiz: QuizQuestion) -> None:
        super().__init__(timeout=30.0)
        self._quiz_service = quiz_service
        self._user_id = user_id
        self._scope_id = scope_id
        self._quiz = quiz
        self._answered = False
        labels = ("A", "B", "C", "D")
        for index, label in enumerate(labels):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=None)
            button.callback = self._make_callback(label)  # type: ignore[method-assign]
            self.add_item(button)

    def _make_callback(self, choice: str):
        async def _callback(interaction: discord.Interaction) -> None:
            await self._handle(interaction, choice)

        return _callback

    async def _handle(self, interaction: discord.Interaction, choice: str) -> None:
        if self._answered:
            return
        try:
            result = await self._quiz_service.answer(self._user_id, self._scope_id, choice)
        except CommandError as exc:
            await interaction.response.send_message(f"⚠️ {exc.message}", ephemeral=True)
            return
        self._answered = True
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
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
        await interaction.response.edit_message(content=text, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        if not self._answered:
            for child in self.children:
                child.disabled = True  # type: ignore[union-attr]


# ======================================================================
# Bot
# ======================================================================

class DiscordAdapter(commands.Bot):
    """Discord driver implementing the platform capability contract."""

    name = "discord"

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
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.voice_states = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        self.settings = settings
        self.container = container
        self.registry = registry
        self.db = database
        self.automod = automod
        self.leveling = leveling
        self.invites = invites
        self.invite_cache: dict[int, list[discord.Invite]] = {}
        self._voice_starts: dict[int, float] = {}
        self._web_runner: Optional[web.AppRunner] = None
        self._background_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect_with_token(self) -> None:
        """Log in and connect using the configured bot token."""
        token = self.settings.discord_token
        if not token:  # pragma: no cover - guarded by settings validation
            raise RuntimeError("DISCORD_BOT_TOKEN is not configured.")
        await self.start(token)

    async def setup_hook(self) -> None:
        self._register_persistent_views()
        self._compile_commands()
        self.news_loop.change_interval(hours=max(self.settings.news_interval_hours, 0.1))
        if self.settings.keep_alive_enabled:
            self._background_tasks.append(asyncio.create_task(self._start_web_server()))
            self._background_tasks.append(asyncio.create_task(self._self_ping_loop()))
        logger.info("Discord adapter initialised (%d commands).", len(self.registry))

    def _register_persistent_views(self) -> None:
        self.add_view(VerificationView())
        self.add_view(RoleToggleView())
        self.add_view(TicketSetupView())
        self.add_view(TicketCloseView())

    def _compile_commands(self) -> None:
        """Build slash commands from the shared registry."""
        for spec in self.registry.for_platform(self.name):
            self.tree.add_command(self._compile_one(spec))

    def _compile_one(self, spec: CommandSpec) -> app_commands.Command:
        """Create one slash command whose signature mirrors the spec."""
        import inspect as _inspect
        from typing import Optional as _Optional

        async def _callback(interaction: discord.Interaction) -> None:
            await self._dispatch(spec, interaction)

        _callback.__name__ = f"cmd_{spec.name.replace('-', '_')}"

        # discord.py derives slash parameters from the callback signature,
        # so synthesise one that matches the CommandSpec's params.
        signature_params: list[_inspect.Parameter] = [
            _inspect.Parameter(
                "interaction",
                _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=discord.Interaction,
            )
        ]
        for param in spec.params:
            annotation: Any = {"user": discord.Member, "int": int, "float": float}.get(
                param.type, str
            )
            if not param.required:
                annotation = _Optional[annotation]  # type: ignore[assignment]
            signature_params.append(
                _inspect.Parameter(
                    param.name,
                    _inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                    default=_inspect.Parameter.empty if param.required else None,
                )
            )
        _callback.__signature__ = _inspect.Signature(signature_params)  # type: ignore[attr-defined]

        command = app_commands.Command(
            name=spec.name,
            description=spec.description[:100],
            callback=_callback,
        )
        for param in spec.params:
            if param.choices:
                command._params[param.name].choices = [  # type: ignore[attr-defined]
                    app_commands.Choice(name=label, value=value)
                    for label, value in param.choices
                ]
        return command

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------
    async def _dispatch(self, spec: CommandSpec, interaction: discord.Interaction) -> None:
        try:
            if spec.admin_only and interaction.guild:
                if not interaction.user.guild_permissions.administrator:  # type: ignore[union-attr]
                    raise CommandError("This command requires administrator permissions.")
            if spec.guild_only and interaction.guild is None:
                raise CommandError("This command can only be used inside a server.")

            args = self._extract_args(spec, interaction)
            ctx = self._build_context(spec, interaction)
            await spec.handler(ctx, args)
        except CommandError as exc:
            await self._send_ephemeral(interaction, f"⚠️ {exc.message}")
        except app_commands.CheckFailure as exc:
            await self._send_ephemeral(interaction, f"⚠️ {exc}")
        except Exception:
            logger.exception("Command /%s failed", spec.name)
            await self._send_ephemeral(
                interaction, "⚠️ Something went wrong while running this command. Please try again."
            )

    def _extract_args(self, spec: CommandSpec, interaction: discord.Interaction) -> dict[str, Any]:
        raw_options = {
            opt["name"]: opt["value"] for opt in (interaction.data or {}).get("options", [])
        }
        args: dict[str, Any] = {}
        for param in spec.params:
            if param.name not in raw_options:
                continue
            value = raw_options[param.name]
            if param.type == "user":
                args[param.name] = self._resolve_user(interaction, int(value))
            elif param.type == "int":
                args[param.name] = int(value)
            elif param.type == "float":
                args[param.name] = float(value)
            else:
                args[param.name] = str(value)
        return args

    @staticmethod
    def _resolve_user(interaction: discord.Interaction, user_id: int) -> UserRef:
        member: Optional[discord.Member] = None
        if interaction.guild:
            member = interaction.guild.get_member(user_id)
        user: Any = member or interaction.client.get_user(user_id)
        display = (
            member.display_name if member else (user.name if user else str(user_id))
        )
        return UserRef(id=str(user_id), display_name=display, raw=member or user)

    def _build_context(self, spec: CommandSpec, interaction: discord.Interaction) -> CommandContext:
        guild = interaction.guild
        user = UserRef(
            id=str(interaction.user.id),
            display_name=interaction.user.display_name,
            raw=interaction.user,
        )
        extras: dict[str, Any] = {}
        if spec.name == "ping":
            extras["latency_ms"] = round(self.latency * 1000)
        if spec.name == "serverinfo" and guild:
            extras["scope_info"] = (
                f"Owner: {guild.owner.mention if guild.owner else 'Unknown'}\n"
                f"Members: {guild.member_count:,}\n"
                f"Created: {guild.created_at.strftime('%Y-%m-%d')}\n"
                f"Text channels: {len(guild.text_channels)} • "
                f"Voice channels: {len(guild.voice_channels)} • Roles: {len(guild.roles)}"
            )
        return CommandContext(
            adapter=self,
            platform=self.name,
            user=user,
            scope_id=str(guild.id) if guild else f"dm:{interaction.user.id}",
            scope_name=guild.name if guild else "Direct Message",
            is_group=guild is not None,
            is_admin=(
                interaction.user.guild_permissions.administrator  # type: ignore[union-attr]
                if guild
                else True
            ),
            channel_id=str(interaction.channel_id) if interaction.channel_id else None,
            raw=interaction,
            extras=extras,
        )

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Ephemeral reply failed", exc_info=True)

    # ------------------------------------------------------------------
    # PlatformAdapter capabilities
    # ------------------------------------------------------------------
    async def send_text(self, ctx: CommandContext, text: str, *, ephemeral: bool = False) -> None:
        interaction: discord.Interaction = ctx.raw

        async def _send() -> None:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(text, ephemeral=ephemeral)

        await self._send_with_retry(_send)

    async def send_quiz(self, ctx: CommandContext, quiz: QuizQuestion) -> None:
        interaction: discord.Interaction = ctx.raw
        view = QuizView(self.container.quiz, ctx.user.id, ctx.scope_id, quiz)
        text = (
            f"🧠 **XESXOR Quiz — {quiz.category}**\n\n"
            f"**{quiz.question}**\n\n{quiz.rendered_options()}\n\n"
            f"30s to answer • {quiz.used_today}/{quiz.daily_limit} used today "
            f"({quiz.remaining_today} left) • Fee: {quiz.fee:.0f} Coins → Win: {quiz.reward:.0f} Coins"
        )

        async def _send() -> None:
            if interaction.response.is_done():
                await interaction.followup.send(text, view=view)
            else:
                await interaction.response.send_message(text, view=view)

        await self._send_with_retry(_send)

    async def delete_messages(self, ctx: CommandContext, amount: int) -> int:
        interaction: discord.Interaction = ctx.raw
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return 0
        deleted = await channel.purge(limit=amount)
        return len(deleted)

    async def apply_member_action(
        self, ctx: CommandContext, user: UserRef, action: str, hours: int = 0
    ) -> bool:
        member = user.raw
        if not isinstance(member, discord.Member):
            return False
        try:
            if action == "kick":
                await member.kick(reason="Automatic kick: 5 warnings reached.")
            elif action == "timeout":
                until = discord.utils.utcnow() + discord.timedelta(hours=hours or 1)
                await member.timeout(until, reason="Automatic timeout: 3 warnings reached.")
            else:
                return False
            return True
        except Exception as exc:
            logger.warning("Member action %s failed for %s: %s", action, user.id, exc)
            return False

    async def apply_role(
        self, ctx: CommandContext, user: UserRef, role_name: str, grant: bool
    ) -> bool:
        member = user.raw
        interaction: Optional[discord.Interaction] = ctx.raw
        guild = interaction.guild if interaction else None
        if guild is None or not isinstance(member, discord.Member):
            return False
        try:
            role = discord.utils.get(guild.roles, name=role_name)
            if role is None and grant:
                role = await guild.create_role(
                    name=role_name, color=discord.Color.purple(), reason="Shop purchase"
                )
            if role is None:
                return False
            if grant:
                await member.add_roles(role, reason="Shop purchase")
            else:
                await member.remove_roles(role)
            return True
        except Exception as exc:
            logger.warning("apply_role(%s) failed: %s", role_name, exc)
            return False

    async def deploy_verify_panel(self, ctx: CommandContext) -> bool:
        interaction: discord.Interaction = ctx.raw
        embed = discord.Embed(
            title="🔒 Server Verification",
            description="Click the button below to verify and unlock the server.",
            color=discord.Color.green(),
        )
        await self._send_with_retry(
            lambda: interaction.channel.send(embed=embed, view=VerificationView())  # type: ignore[union-attr]
        )
        return True

    async def deploy_roles_panel(self, ctx: CommandContext) -> bool:
        interaction: discord.Interaction = ctx.raw
        embed = discord.Embed(
            title="🎨 Self-Assign Roles",
            description=(
                "Click the buttons below to assign or remove roles:\n\n• "
                + "\n• ".join(ROLE_PANEL)
            ),
            color=discord.Color.blue(),
        )
        await self._send_with_retry(
            lambda: interaction.channel.send(embed=embed, view=RoleToggleView())  # type: ignore[union-attr]
        )
        return True

    async def deploy_ticket_panel(self, ctx: CommandContext) -> bool:
        interaction: discord.Interaction = ctx.raw
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Click below to open a private ticket.",
            color=discord.Color.blue(),
        )
        await self._send_with_retry(
            lambda: interaction.channel.send(embed=embed, view=TicketSetupView())  # type: ignore[union-attr]
        )
        return True

    async def open_ticket(self, ctx: CommandContext, ticket: TicketRecord) -> bool:
        interaction: discord.Interaction = ctx.raw
        guild = interaction.guild
        if guild is None:
            return False
        try:
            category = discord.utils.get(guild.categories, name="Support Tickets")
            if category is None:
                category = await guild.create_category("Support Tickets")
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                ),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            channel = await guild.create_text_channel(
                f"ticket-{ticket.ticket_id}-{interaction.user.name}",
                category=category,
                overwrites=overwrites,
            )
            embed = discord.Embed(
                title=f"🎫 Ticket #{ticket.ticket_id}: {ticket.subject}",
                description=ticket.details[:4000],
                color=discord.Color.blue(),
            )
            embed.set_author(
                name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url
            )
            await channel.send(
                content=f"{interaction.user.mention} Welcome! Staff will be with you shortly.",
                embed=embed,
                view=TicketCloseView(),
            )
            return True
        except Exception as exc:
            logger.warning("open_ticket failed: %s", exc)
            return False

    async def publish_suggestion(self, ctx: CommandContext, content: str, suggestion_id: int) -> bool:
        interaction: discord.Interaction = ctx.raw
        guild = interaction.guild
        if guild is None:
            return False
        channel = discord.utils.get(guild.text_channels, name="suggestions")
        if channel is None:
            try:
                channel = await guild.create_text_channel("suggestions")
            except Exception as exc:
                logger.warning("suggestions channel create failed: %s", exc)
                return False
        embed = discord.Embed(
            title=f"💡 Suggestion #{suggestion_id}", description=content, color=discord.Color.gold()
        )
        embed.set_author(
            name=ctx.user.display_name,
            icon_url=interaction.user.display_avatar.url,
        )
        embed.add_field(name="Status", value="🟡 Under Review", inline=False)
        message = await self._send_with_retry(lambda: channel.send(embed=embed))
        if message:
            await message.add_reaction("👍")
            await message.add_reaction("👎")
        return message is not None

    async def publish_event(
        self, ctx: CommandContext, title: str, time_str: str, description: str
    ) -> bool:
        interaction: discord.Interaction = ctx.raw
        if interaction.channel is None:
            return False
        embed = discord.Embed(
            title=f"📅 Community Event: {title}",
            description=description,
            color=discord.Color.purple(),
        )
        embed.add_field(name="⏰ Time", value=time_str, inline=False)
        embed.set_footer(text=f"Hosted by {ctx.user.display_name}")
        message = await self._send_with_retry(lambda: interaction.channel.send(embed=embed))  # type: ignore[union-attr]
        if message:
            await message.add_reaction("✅")
            await message.add_reaction("❌")
        return message is not None

    async def deliver_signal_question(self, ctx: CommandContext, question: str) -> bool:
        interaction: discord.Interaction = ctx.raw
        guild = interaction.guild
        if guild is None:
            return False
        inbox = discord.utils.get(guild.text_channels, name="signal-inbox")
        if inbox is None:
            return False
        embed = discord.Embed(
            title="📡 Incoming Signal Transmission",
            description=question[:4000],
            color=0x57F287,
        )
        embed.set_author(
            name=ctx.user.display_name, icon_url=interaction.user.display_avatar.url
        )
        message = await self._send_with_retry(lambda: inbox.send(embed=embed))
        return message is not None

    # ------------------------------------------------------------------
    # Reliability helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _send_with_retry(send_call, retries: int = 4):
        """Retry through temporary Cloudflare/Discord 429 windows."""
        for attempt in range(retries):
            try:
                return await send_call()
            except discord.HTTPException as exc:
                if exc.status == 429 and attempt < retries - 1:
                    await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                    continue
                raise
        return None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    async def on_ready(self) -> None:
        logger.info("Discord logged in as %s (%d guilds)", self.user, len(self.guilds))
        for guild in self.guilds:
            try:
                self.invite_cache[guild.id] = await guild.invites()
            except Exception as exc:
                logger.warning("Invite cache error for %s: %s", guild.name, exc)
            if self.settings.discord_auto_setup:
                try:
                    await self._bootstrap_guild(guild)
                except Exception as exc:
                    logger.error("Bootstrap error for %s: %s", guild.name, exc)
        if not self.news_loop.is_running():
            self.news_loop.start()

    async def on_invite_create(self, invite: discord.Invite) -> None:
        try:
            self.invite_cache[invite.guild.id] = await invite.guild.invites()
        except Exception:
            pass

    async def on_invite_delete(self, invite: discord.Invite) -> None:
        try:
            self.invite_cache[invite.guild.id] = await invite.guild.invites()
        except Exception:
            pass

    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        inviter: Optional[discord.Member] = None
        try:
            old_invites = self.invite_cache.get(guild.id, [])
            new_invites = await guild.invites()
            self.invite_cache[guild.id] = new_invites
            old_map = {inv.code: inv.uses for inv in old_invites}
            for new_invite in new_invites:
                if new_invite.uses > old_map.get(new_invite.code, 0):
                    inviter = new_invite.inviter
                    break
        except Exception as exc:
            logger.warning("Invite tracking error: %s", exc)

        referrer_id = None
        if inviter and not inviter.bot and inviter.id != member.id:
            referrer_id = str(inviter.id)
        self.invites.register_join(str(member.id), str(guild.id), referrer_id)

        verify_channel = discord.utils.get(guild.text_channels, name="verify")
        target_channel = verify_channel or discord.utils.get(
            guild.text_channels, name="welcome"
        ) or discord.utils.get(guild.text_channels, name="general-chat")
        if target_channel is None:
            return

        verify_ref = f"<#{verify_channel.id}>" if verify_channel else "the **#verify** channel"
        lines = [
            f"🎉 Hey {member.mention}, welcome to **{guild.name}**! 🚀",
            "",
            "🔒 **ACCOUNT UNVERIFIED** — you currently have read-only access.",
            f"👉 Go to {verify_ref} and click **✅ Verify to Access Server** to unlock chatting!",
        ]
        if inviter and not inviter.bot and inviter.id != member.id:
            lines.append(f"📩 Invited by {inviter.mention} • Earn 10 Coins per invite!")
        try:
            await self._send_with_retry(
                lambda: target_channel.send("\n".join(lines))  # type: ignore[union-attr]
            )
        except Exception as exc:
            logger.warning("Welcome message error: %s", exc)

        try:
            await member.send(
                f"👋 Thanks for joining **{guild.name}**! To unlock chatting, open "
                f"{verify_ref} and click **✅ Verify to Access Server**."
            )
        except Exception:
            pass

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if after.channel and not before.channel:
            self._voice_starts[member.id] = time.time()
        elif before.channel and not after.channel:
            start = self._voice_starts.pop(member.id, None)
            if start:
                minutes = int((time.time() - start) / 60)
                self.leveling.record_voice_minutes(str(member.id), str(member.guild.id), minutes)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # Verification gate: enforced only when the guild has a #verify
        # channel (created by our bootstrap), so foreign servers are never
        # affected.
        has_verify_channel = any(ch.name == "verify" for ch in message.guild.text_channels)
        if has_verify_channel:
            is_verified = (
                any(role.name == "Member" for role in message.author.roles)
                or message.author.guild_permissions.administrator  # type: ignore[union-attr]
            )
            if not is_verified:
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    verify_channel = discord.utils.get(message.guild.text_channels, name="verify")
                    ref = f"<#{verify_channel.id}>" if verify_channel else "#verify"
                    await message.channel.send(
                        f"🔒 {message.author.mention} **Done reading? Check {ref}! "
                        "Verify and hack the planet.**",
                        delete_after=10,
                    )
                except Exception:
                    pass
                return

        # Automod (staff/team sections exempt).
        is_team = (
            (message.channel.category and "team" in message.channel.category.name.lower())  # type: ignore[union-attr]
            or "team" in message.channel.name.lower()
            or message.author.guild_permissions.administrator  # type: ignore[union-attr]
            or any(role.name.lower() in STAFF_ROLE_NAMES for role in message.author.roles)
        )
        if not is_team:
            violation = self.automod.check(
                str(message.author.id), str(message.guild.id), message.content
            )
            if violation:
                try:
                    await message.delete()
                except Exception:
                    pass
                if violation.action == "delete_notify" and violation.notice:
                    try:
                        await message.channel.send(
                            f"🚨 {message.author.mention} {violation.notice}", delete_after=10
                        )
                    except Exception:
                        pass
                return

        # Economy / progression.
        self.container.economy.ensure_user(str(message.author.id), str(message.guild.id))
        level_up, milestone = self.leveling.record_message(
            str(message.author.id), str(message.guild.id)
        )
        if milestone:
            try:
                await self._send_with_retry(
                    lambda: message.channel.send(
                        f"🪙 {message.author.mention} Milestone reached! "
                        f"**{milestone.messages:,} messages** sent — you earned "
                        f"**{milestone.coins:.0f} Coin**! 🎉"
                    )
                )
            except Exception:
                pass
        if level_up:
            try:
                await self._send_with_retry(
                    lambda: message.channel.send(
                        f"🚀 {message.author.mention} leveled up to "
                        f"**Level {level_up.new_level}**! 🎉"
                    )
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Guild bootstrap (opt-in via DISCORD_AUTO_SETUP)
    # ------------------------------------------------------------------
    async def _bootstrap_guild(self, guild: discord.Guild) -> None:
        """Create the standard XESXOR layout in *guild* (opt-in only)."""
        everyone = guild.default_role
        member_role = discord.utils.get(guild.roles, name="Member")
        if member_role is None:
            member_role = await guild.create_role(
                name="Member", color=discord.Color.green(), reason="Verification role"
            )

        comm_cat = discord.utils.find(
            lambda c: "community" in c.name.lower(), guild.categories
        )
        if comm_cat is None:
            comm_cat = await guild.create_category("☕ COMMUNITY & LOUNGE")
        if not discord.utils.get(guild.text_channels, name="bot-commands"):
            await guild.create_text_channel(
                "bot-commands",
                category=comm_cat,
                topic="Execute bot commands and economy tools here.",
            )

        verify_ch = discord.utils.get(guild.text_channels, name="verify")
        if verify_ch is None:
            verify_ow = {
                everyone: discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True,
                    send_messages=False, add_reactions=False,
                ),
                member_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False
                ),
            }
            verify_ch = await guild.create_text_channel(
                "verify", overwrites=verify_ow, topic="Verify your account to unlock the server."
            )
            embed = discord.Embed(
                title="🔒 Server Verification",
                description=(
                    "Welcome! Click the button below to verify your account and "
                    "unlock all channels."
                ),
                color=discord.Color.green(),
            )
            await verify_ch.send(embed=embed, view=VerificationView())

        for name, topic in (
            ("coding-news", "Programming, development, and tech industry updates."),
            ("cyber-news", "Cybersecurity, threats, vulnerabilities, and breaches."),
        ):
            if not discord.utils.get(guild.text_channels, name=name):
                await guild.create_text_channel(name, topic=topic)

        # Strict lockdown for public channels.
        bot_only = {"verify", "coding-news", "cyber-news", "signal-inbox"}
        for channel in guild.text_channels:
            base_ow = channel.overwrites_for(everyone)
            cat_ow = channel.category.overwrites_for(everyone) if channel.category else None
            view_denied = base_ow.view_channel is False or (
                base_ow.view_channel is None
                and cat_ow
                and cat_ow.view_channel is False
            )
            if view_denied:
                continue
            await channel.set_permissions(
                everyone,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True,
                    send_messages=False, add_reactions=False,
                ),
            )
            can_send = channel.name not in bot_only
            await channel.set_permissions(
                member_role,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, read_message_history=True,
                    send_messages=can_send, add_reactions=can_send,
                ),
            )
        logger.info("Bootstrapped guild: %s", guild.name)

    # ------------------------------------------------------------------
    # News loop
    # ------------------------------------------------------------------
    @tasks.loop(hours=8)
    async def news_loop(self) -> None:
        if not self.container.news.due():
            return
        posted_any = False
        for guild in self.guilds:
            try:
                coding_ch = discord.utils.get(guild.text_channels, name="coding-news")
                cyber_ch = discord.utils.get(guild.text_channels, name="cyber-news")
                if coding_ch:
                    text = await self.container.news.generate("coding")
                    await self._send_with_retry(lambda: coding_ch.send(text))  # type: ignore[union-attr]
                    posted_any = True
                if cyber_ch:
                    text = await self.container.news.generate("cyber")
                    await self._send_with_retry(lambda: cyber_ch.send(text))  # type: ignore[union-attr]
                    posted_any = True
            except Exception as exc:
                logger.error("News posting failed for %s: %s", guild.name, exc)
        if posted_any:
            self.container.news.mark_posted()

    @news_loop.before_loop
    async def _before_news_loop(self) -> None:
        await self.wait_until_ready()

    # ------------------------------------------------------------------
    # Keep-alive web server (free-tier hosts)
    # ------------------------------------------------------------------
    async def _start_web_server(self) -> None:
        app = web.Application()
        app.router.add_get(
            "/", lambda request: web.Response(text="XESXOR Bot running 24/7 ✅")
        )
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, "0.0.0.0", self.settings.port)
        await site.start()
        logger.info("Keep-alive web server on port %d", self.settings.port)

    async def _self_ping_loop(self) -> None:
        """Ping ourselves so free-tier hosts do not idle-sleep."""
        await self.wait_until_ready()
        url = f"http://127.0.0.1:{self.settings.port}"
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        logger.debug("Self-ping: HTTP %s", resp.status)
            except Exception as exc:
                logger.debug("Self-ping failed: %s", exc)
            await asyncio.sleep(600)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    async def close(self) -> None:
        logger.info("Shutting down Discord adapter...")
        if self.news_loop.is_running():
            self.news_loop.cancel()
        for task in self._background_tasks:
            task.cancel()
        if self._web_runner is not None:
            try:
                await self._web_runner.cleanup()
            except Exception:
                pass
        await super().close()
