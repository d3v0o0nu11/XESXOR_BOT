"""Unified command definitions.

Every feature is registered once against the :class:`CommandRegistry`.
Discord compiles them into slash commands, Telegram into ``/commands`` —
one implementation, both platforms.
"""

from __future__ import annotations

from typing import Any

from xesxor.core.context import CommandContext
from xesxor.core.errors import CommandError
from xesxor.core.models import UserRef
from xesxor.core.registry import CommandParam, CommandRegistry, CommandSpec
from xesxor.core.services import (
    CommunityService,
    EconomyService,
    ModerationService,
    NewsService,
    QuizService,
)
from xesxor.core.services.ai import AIService
from xesxor.core.services.economy import CONTRIBUTION_REWARDS, SHOP_ITEMS
from xesxor.core.services.quiz import CATEGORIES


class ServiceContainer:
    """Bundle of shared services handed to every command handler."""

    def __init__(
        self,
        economy: EconomyService,
        quiz: QuizService,
        moderation: ModerationService,
        community: CommunityService,
        ai: AIService,
        news: NewsService,
    ) -> None:
        self.economy = economy
        self.quiz = quiz
        self.moderation = moderation
        self.community = community
        self.ai = ai
        self.news = news


def _require_user_arg(args: dict[str, Any], name: str) -> UserRef:
    user = args.get(name)
    if user is None:
        raise CommandError(f"Missing required user: `{name}`.")
    return user


# ----------------------------------------------------------------------
# Economy
# ----------------------------------------------------------------------

def _build_balance_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target: UserRef = args.get("member") or ctx.user
        snapshot = svc.economy.wallet(target.id, ctx.scope_id, target.display_name)
        await ctx.reply(
            f"💳 **Wallet — {snapshot.display_name}**\n"
            f"Coins 🪙: **{snapshot.coins:,.2f}**\n"
            f"Level: **{snapshot.level}** • Rep: **{snapshot.reputation}**\n"
            f"Messages: {snapshot.messages:,} • Voice: {snapshot.voice_minutes:,}m"
        )

    return CommandSpec(
        name="balance",
        description="View your Coins balance, level and reputation",
        handler=handler,
        params=(
            CommandParam("member", "User to inspect (defaults to you)", "user", required=False),
        ),
        usage="/balance [@user]",
    )


def _build_daily_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        result = svc.economy.claim_daily(ctx.user.id, ctx.scope_id)
        await ctx.reply(
            f"🎁 Daily claimed! You earned **{result.reward:.1f} Coins** 🪙 "
            f"(Streak: {result.streak} days | Multiplier: {result.multiplier:g}x)"
        )

    return CommandSpec(
        name="daily",
        description="Claim your daily check-in coin reward with streak multiplier",
        handler=handler,
        usage="/daily",
    )


def _build_gift_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target = _require_user_arg(args, "member")
        amount = float(args["amount"])
        svc.economy.gift(ctx.user.id, ctx.scope_id, target.id, amount)
        await ctx.reply(f"🎁 Gifted **{amount:,.2f} Coins** to {target.display_name}!")

    return CommandSpec(
        name="gift",
        description="Gift Coins to another user",
        handler=handler,
        params=(
            CommandParam("member", "Recipient", "user"),
            CommandParam("amount", "Amount of coins to gift", "float"),
        ),
        usage="/gift @user 100",
    )


def _build_shop_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        lines = ["**Permanent Roles (earned Coins 🪙):**"]
        for item in SHOP_ITEMS:
            lines.append(f"• **{item.name}** — {item.price:,.0f} Coins")
        lines.append("\nUse `/buy <item>` to purchase. Coins are free — earn them by chatting,")
        lines.append("quizzes, dailies and community contributions!")
        await ctx.reply("\n".join(lines))

    return CommandSpec(
        name="shop",
        description="Browse permanent roles purchasable with earned Coins",
        handler=handler,
        usage="/shop",
    )


def _build_buy_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        item_name = str(args["item"])
        result = svc.economy.buy(ctx.user.id, ctx.scope_id, item_name)
        await ctx.adapter.apply_role(ctx, ctx.user, result.item, grant=True)
        await ctx.reply(f"🎉 Purchased **{result.item}** for {result.price:,.0f} Coins!")

    choices = tuple((item.name, item.name) for item in SHOP_ITEMS)
    return CommandSpec(
        name="buy",
        description="Purchase a permanent role from the shop",
        handler=handler,
        params=(CommandParam("item", "Item to buy", "choice", choices=choices),),
        usage="/buy Contributor",
    )


def _build_inventory_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target: UserRef = args.get("member") or ctx.user
        items = svc.economy.inventory(target.id, ctx.scope_id)
        listing = "\n".join(f"• {item}" for item in items) if items else "Inventory is empty."
        await ctx.reply(f"🎒 **Inventory — {target.display_name}**\n{listing}")

    return CommandSpec(
        name="inventory",
        description="View your purchased items and roles",
        handler=handler,
        params=(
            CommandParam("member", "User to inspect (defaults to you)", "user", required=False),
        ),
        usage="/inventory [@user]",
    )


def _build_thanks_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target = _require_user_arg(args, "member")
        svc.economy.thanks(ctx.user.id, ctx.scope_id, target.id)
        await ctx.reply(
            f"🌟 {ctx.display_name} thanked {target.display_name}! (+1 Rep, +20 Coins 🪙)"
        )

    return CommandSpec(
        name="thanks",
        description="Give reputation points and coins to a helpful member",
        handler=handler,
        params=(CommandParam("member", "Member to thank", "user"),),
        usage="/thanks @user",
    )


def _build_award_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target = _require_user_arg(args, "member")
        activity = str(args["activity"])
        coins = svc.economy.award_contribution(ctx.scope_id, target.id, activity)
        await ctx.reply(
            f"🏆 Awarded **{coins:,.0f} Coins** to {target.display_name} "
            f"for contribution: **{activity}**!"
        )

    choices = tuple((name, name) for name in CONTRIBUTION_REWARDS)
    return CommandSpec(
        name="award-contribution",
        description="Award contribution coins to a member (Staff only)",
        handler=handler,
        params=(
            CommandParam("member", "Recipient", "user"),
            CommandParam("activity", "Contribution type", "choice", choices=choices),
        ),
        admin_only=True,
        usage="/award-contribution @user \"Bug Report\"",
        aliases=("award_contribution",),
    )


# ----------------------------------------------------------------------
# Quiz
# ----------------------------------------------------------------------

def _build_quiz_cmd(svc: ServiceContainer, key: str) -> CommandSpec:
    category = CATEGORIES[key]

    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        question = await svc.quiz.start(ctx.user.id, ctx.scope_id, key)
        await ctx.send_quiz(question)

    command_name = f"quiz-{key}" if key != "it" else "quiz-it"
    return CommandSpec(
        name=command_name,
        description=f"{category} quiz — 10/day, 10 Coins fee, 20 Coins reward",
        handler=handler,
        usage=f"/{command_name}",
        aliases=(command_name.replace("-", "_"),),
    )


# ----------------------------------------------------------------------
# AI & community
# ----------------------------------------------------------------------

def _build_ask_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        prompt = str(args["prompt"])
        await ctx.reply("✨ *Thinking...*", ephemeral=True)
        answer = await svc.ai.ask(prompt, platform_context=ctx.scope_name or "community server")
        await ctx.reply(f"✨ **XESXOR AI Assistant**\n\n{answer}")

    return CommandSpec(
        name="ask",
        description="Ask XESXOR AI assistant a question",
        handler=handler,
        params=(CommandParam("prompt", "Your question or prompt", "str"),),
        usage="/ask How do I center a div?",
    )


def _build_question_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        question = str(args["question"])
        svc.community.add_signal_question(ctx.user.id, ctx.scope_id, question)
        delivered = await ctx.adapter.deliver_signal_question(ctx, question)
        count = svc.community.signal_question_count(ctx.scope_id)
        if delivered:
            await ctx.reply(
                "📡 Transmission received! Your question has been queued for the "
                f"weekly XESXOR Signal ({count} in queue)."
            )
        else:
            await ctx.reply(
                f"📡 Question saved ({count} in queue). Staff will review it before the Signal."
            )

    return CommandSpec(
        name="question",
        description="Submit your question for the weekly XESXOR Signal",
        handler=handler,
        params=(CommandParam("question", "Your question for the live Signal", "str"),),
        usage="/question What is Kerberoasting?",
    )


def _build_suggest_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        content = str(args["suggestion"])
        suggestion_id = svc.community.add_suggestion(ctx.user.id, ctx.scope_id, content)
        await ctx.adapter.publish_suggestion(ctx, content, suggestion_id)
        await ctx.reply("💡 Suggestion submitted! Staff will review it.")

    return CommandSpec(
        name="suggest",
        description="Submit a server suggestion",
        handler=handler,
        params=(CommandParam("suggestion", "Your suggestion description", "str"),),
        usage="/suggest Add a CTF archive channel",
    )


def _build_event_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        title = str(args["title"])
        time_str = str(args["time_str"])
        description = str(args["description"])
        svc.community.add_event(ctx.user.id, ctx.scope_id, title, time_str, description)
        await ctx.adapter.publish_event(ctx, title, time_str, description)
        await ctx.reply("📅 Event scheduled!")

    return CommandSpec(
        name="event",
        description="Schedule a community event",
        handler=handler,
        params=(
            CommandParam("title", "Event title", "str"),
            CommandParam("time_str", "When is it?", "str"),
            CommandParam("description", "Details", "str"),
        ),
        usage="/event Workshop \"Sunday 9PM EEST\" \"Live hacking session\"",
    )


def _build_ticket_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        subject = str(args.get("subject") or "Support request")
        details = str(args.get("details") or "(no details provided)")
        ticket = svc.community.open_ticket(ctx.user.id, ctx.scope_id, subject, details)
        opened = await ctx.adapter.open_ticket(ctx, ticket)
        if opened:
            await ctx.reply(f"🎫 Ticket #{ticket.ticket_id} created — staff will be with you shortly.")
        else:
            await ctx.reply(
                f"🎫 Ticket #{ticket.ticket_id} recorded. Staff has been notified."
            )

    return CommandSpec(
        name="ticket",
        description="Open a support ticket",
        handler=handler,
        params=(
            CommandParam("subject", "Issue / subject", "str", required=False),
            CommandParam("details", "Detailed explanation", "str", required=False),
        ),
        usage="/ticket Login issue \"2FA code not accepted\"",
    )


# ----------------------------------------------------------------------
# Moderation & utility
# ----------------------------------------------------------------------

def _build_warn_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        target = _require_user_arg(args, "member")
        reason = str(args["reason"])
        result = svc.moderation.warn(target.id, ctx.scope_id, ctx.user.id, reason)

        message = (
            f"⚠️ Warned {target.display_name} for `{reason}`. "
            f"**Strikes: {result.strikes}/5**"
        )
        if result.action:
            applied = await ctx.adapter.apply_member_action(
                ctx, target, result.action, result.action_hours
            )
            if result.action == "timeout":
                message += (
                    f"\n⏱️ {target.display_name} has been timed out for "
                    f"{result.action_hours} hour(s) (3 strikes)."
                    if applied
                    else "\n⚠️ Escalation failed — check the bot's role position/permissions."
                )
            elif result.action == "kick":
                message += (
                    f"\n🚨 {target.display_name} has been kicked for reaching 5 strikes!"
                    if applied
                    else "\n⚠️ Kick failed — check the bot's role position/permissions."
                )
        await ctx.reply(message)

    return CommandSpec(
        name="warn",
        description="Warn a user (auto-escalates: 3 = timeout, 5 = kick)",
        handler=handler,
        params=(
            CommandParam("member", "User to warn", "user"),
            CommandParam("reason", "Reason for the warning", "str"),
        ),
        admin_only=True,
        guild_only=True,
        usage="/warn @user spamming",
    )


def _build_purge_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        amount = int(args["amount"])
        if amount < 1 or amount > 100:
            raise CommandError("Amount must be between 1 and 100.")
        deleted = await ctx.adapter.delete_messages(ctx, amount)
        await ctx.reply(f"🧹 Deleted **{deleted}** message(s).")

    return CommandSpec(
        name="purge",
        description="Bulk delete recent messages (1-100)",
        handler=handler,
        params=(CommandParam("amount", "How many messages to delete", "int"),),
        admin_only=True,
        guild_only=True,
        usage="/purge 50",
    )


def _build_serverinfo_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        # Adapter-specific detail block lives on the context extras.
        info = ctx.extras.get("scope_info", "No additional information available.")
        await ctx.reply(f"🛡️ **Info — {ctx.scope_name}**\n{info}")

    return CommandSpec(
        name="serverinfo",
        description="Display server / chat information",
        handler=handler,
        usage="/serverinfo",
        aliases=("chatinfo",),
    )


def _build_ping_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        latency = ctx.extras.get("latency_ms")
        if latency is None:
            await ctx.reply("Pong! 🏓")
        else:
            await ctx.reply(f"Pong! 🏓 Latency: `{latency}ms`")

    return CommandSpec(
        name="ping",
        description="Check bot latency",
        handler=handler,
        usage="/ping",
    )


# ----------------------------------------------------------------------
# Setup panels (admin)
# ----------------------------------------------------------------------

def _build_setup_verify_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        ok = await ctx.adapter.deploy_verify_panel(ctx)
        if ok:
            await ctx.reply("✅ Verification panel deployed!")
        else:
            await ctx.reply("⚠️ Could not deploy the verification panel here.")

    return CommandSpec(
        name="setup-verify",
        description="Deploy the verification panel (Staff only)",
        handler=handler,
        admin_only=True,
        usage="/setup-verify",
        aliases=("setup_verify",),
    )


def _build_setup_roles_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        ok = await ctx.adapter.deploy_roles_panel(ctx)
        if ok:
            await ctx.reply("🎨 Self-role panel deployed!")
        else:
            await ctx.reply("⚠️ Could not deploy the role panel here.")

    return CommandSpec(
        name="setup-roles",
        description="Deploy the self-role panel",
        handler=handler,
        usage="/setup-roles",
        aliases=("setup_roles",),
    )


def _build_setup_ticket_cmd(svc: ServiceContainer) -> CommandSpec:
    async def handler(ctx: CommandContext, args: dict[str, Any]) -> None:
        ok = await ctx.adapter.deploy_ticket_panel(ctx)
        if ok:
            await ctx.reply("🎫 Ticket panel deployed!")
        else:
            await ctx.reply("⚠️ Could not deploy the ticket panel here.")

    return CommandSpec(
        name="setup-ticket",
        description="Deploy the support-ticket panel (Staff only)",
        handler=handler,
        admin_only=True,
        usage="/setup-ticket",
        aliases=("setup_ticket",),
    )


# ----------------------------------------------------------------------
# Registry assembly
# ----------------------------------------------------------------------

def build_registry(svc: ServiceContainer) -> CommandRegistry:
    """Create the full command registry shared by every platform adapter.

    Args:
        svc: Shared service container.

    Returns:
        A registry containing every cross-platform command.
    """
    registry = CommandRegistry()

    # Economy
    registry.register(_build_balance_cmd(svc))
    registry.register(_build_daily_cmd(svc))
    registry.register(_build_gift_cmd(svc))
    registry.register(_build_shop_cmd(svc))
    registry.register(_build_buy_cmd(svc))
    registry.register(_build_inventory_cmd(svc))
    registry.register(_build_thanks_cmd(svc))
    registry.register(_build_award_cmd(svc))

    # Quizzes
    for key in CATEGORIES:
        registry.register(_build_quiz_cmd(svc, key))

    # AI & community
    registry.register(_build_ask_cmd(svc))
    registry.register(_build_question_cmd(svc))
    registry.register(_build_suggest_cmd(svc))
    registry.register(_build_event_cmd(svc))
    registry.register(_build_ticket_cmd(svc))

    # Moderation & utility
    registry.register(_build_warn_cmd(svc))
    registry.register(_build_purge_cmd(svc))
    registry.register(_build_serverinfo_cmd(svc))
    registry.register(_build_ping_cmd(svc))

    # Setup panels
    registry.register(_build_setup_verify_cmd(svc))
    registry.register(_build_setup_roles_cmd(svc))
    registry.register(_build_setup_ticket_cmd(svc))

    return registry
