import os
import random
import asyncio
import re
from datetime import datetime, timezone
import discord
from groq import AsyncGroq
from discord.ext import commands, tasks
from dotenv import load_dotenv
from commands import send_log, disabled_cmds, blocked
from help_ui import HELP_PAGES, HelpPaginator


load_dotenv()

DEV_ID    = int(os.getenv("DEV_ID", "0"))
ai_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

# ── ROADMAP STORAGE ───────────────────────────────────────────────────────────
roadmap_items = {}  # { id: { title, status, added_by } }
roadmap_counter = 0

STATUS_ICONS = {
    "planned":     "📋",
    "in-progress": "🔨",
    "done":        "✅",
    "cancelled":   "❌",
}

STATUS_COLORS = {
    "planned":     0x5865F2,
    "in-progress": 0xFEE75C,
    "done":        0x57F287,
    "cancelled":   0xFF6B6B,
}

VALID_STATUSES = list(STATUS_ICONS.keys())

# ── COG ───────────────────────────────────────────────────────────────────────
class ExtraCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /lua ──────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="lua", description="Look up any Lua or Roblox API term, function, or concept")
    @discord.app_commands.describe(query="What do you want to look up? e.g. table.insert, TweenService, metatables")
    async def lua(self, interaction: discord.Interaction, query: str):
        if "lua" in disabled_cmds:
            await interaction.response.send_message("🚫 This command is currently disabled.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            response = await ai_client.chat.completions.create(
                model      = "llama-3.1-8b-instant",
                max_tokens = 1024,
                messages   = [
                    {
                        "role": "system",
                        "content": (
                            "You are a Lua and Roblox Studio documentation assistant. "
                            "When given a term, function, or concept, explain it clearly and concisely. "
                            "Always include: a short description, syntax, parameters, return values if any, and a short code example. "
                            "Format your response for Discord using markdown code blocks with ```lua for code. "
                            "If it's a Roblox-specific API, mention the service it belongs to. "
                            "Keep it focused and under 1500 characters total. "
                            "Never mention AI, Claude, or any model name."
                        )
                    },
                    {"role": "user", "content": f"Look up: {query}"}
                ],
            )
            result = response.choices[0].message.content
        except Exception as e:
            await interaction.followup.send(f"❌ Lookup failed: `{e}`")
            return

        embed = discord.Embed(
            title       = f"📖 Lua / Roblox — `{query}`",
            description = result[:4096],
            color       = 0x00B4FF,
        )
        embed.set_footer(text="CFrame Bot · Lua Dictionary")
        await interaction.followup.send(embed=embed)
        await send_log(self.bot, "COMMAND", f"{interaction.user} looked up **`{query}`** in Lua dictionary.")

    # ── /help ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="help", description="Interactive help menu")
    async def help(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return

        paginator = HelpPaginator(author_id=interaction.user.id, pages=HELP_PAGES)
        await interaction.response.send_message(embed=paginator._build_embed(), view=paginator)
        return

        embed = discord.Embed(
            title="🤖 CFrame Bot Commands",
            description="Quick navigation for everything CFrame can do.",
            color=0x5865F2,
        )


        embed.add_field(name="🧭 General", value="`/help`\n`/status`\n`/serverinfo`\n`/userinfo`", inline=True)
        embed.add_field(name="🎮 Roblox", value="`/players`\n`/chat`\n`/lua`\n`/roadmap`", inline=True)
        embed.add_field(name="�️ Server Tools", value="`/modhelp`\n`/modsettings`\n`/setlogchannel`\n`/clearlogchannel`\n`/setwelcomechannel`\n`/setwelcomemessage`\n`/disablewelcome`\n`/welcometest`", inline=False)
        embed.add_field(name="⚔️ Moderation", value="`/warn`\n`/warnings`\n`/clearwarnings`\n`/mute`\n`/unmute`\n`/kick`\n`/ban`\n`/unban`\n`/clear`\n`/slowmode`", inline=False)
        embed.add_field(name="🎉 Community", value="`/gstart`\n`/gend`\n`/greroll`\n`/level`\n`/leaderboard`\n`/toggleleveling`\n`/toggleantispam`\n`/setantispamthreshold`\n`/autorole`\n`/reactionrole`", inline=False)
        embed.add_field(name="🛠️ Staff", value="`/devlog`\n`/updates`\n`/hype`\n`/countdown`\n`/patchpreview`\n`/testing`\n`/dev`", inline=False)
        embed.set_footer(text="CFrame Bot · Help")
        await interaction.response.send_message(embed=embed)

    # ── /serverinfo ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="serverinfo", description="Show information about this server")
    async def serverinfo(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🏠 {guild.name}",
            description=f"Server ID: {guild.id}",
            color=0x5865F2,
        )
        embed.add_field(name="👑 Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="👥 Members", value=f"{guild.member_count:,}", inline=True)
        embed.add_field(name="🚀 Boosts", value=str(guild.premium_subscription_count or 0), inline=True)
        embed.add_field(name="📅 Created", value=discord.utils.format_dt(guild.created_at, style="F"), inline=False)
        embed.add_field(name="🧩 Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="🎭 Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="🔐 Verification", value=str(guild.verification_level).replace("_", " ").title(), inline=True)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text="CFrame Bot · Server Info")
        await interaction.response.send_message(embed=embed)

    # ── /userinfo ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="userinfo", description="Show information about a user")
    @discord.app_commands.describe(user="The user to inspect")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        if await blocked(interaction): return
        target = user or interaction.user
        member = target if isinstance(target, discord.Member) else None

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            description=target.mention,
            color=0x57F287,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🆔 ID", value=str(target.id), inline=True)
        embed.add_field(name="🤖 Bot", value="Yes" if target.bot else "No", inline=True)
        embed.add_field(name="📅 Created", value=discord.utils.format_dt(target.created_at, style="F"), inline=False)
        if member:
            embed.add_field(name="📍 Joined Server", value=discord.utils.format_dt(member.joined_at, style="F") if member.joined_at else "Unknown", inline=True)
            embed.add_field(name="🎭 Roles", value=str(len(member.roles) - 1), inline=True)
            top_role = member.top_role.mention if member.top_role and member.top_role != member.guild.default_role else "None"
            embed.add_field(name="🏅 Top Role", value=top_role, inline=True)
        embed.set_footer(text="CFrame Bot · User Info")
        await interaction.response.send_message(embed=embed)

    # ── /roadmap ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="roadmap", description="View or manage the game roadmap")
    @discord.app_commands.describe(
        action    = "view, add, update, remove, or clear",
        title     = "Feature title (used with 'add')",
        status    = "planned, in-progress, done, or cancelled",
        item_id   = "Item ID to update or remove (shown in /roadmap view)",
    )
    async def roadmap(
        self,
        interaction: discord.Interaction,
        action: str,
        title: str = None,
        status: str = None,
        item_id: int = None,
    ):
        if "roadmap" in disabled_cmds:
            await interaction.response.send_message("🚫 This command is currently disabled.", ephemeral=True)
            return

        global roadmap_counter
        action = action.lower()
        is_staff = interaction.user.guild_permissions.manage_messages

        # ── view ──────────────────────────────────────────────────────────────
        if action == "view":
            if not roadmap_items:
                await interaction.response.send_message(
                    "📋 The roadmap is empty. Staff can add items with `/roadmap add`.", ephemeral=False)
                return

            embed = discord.Embed(
                title="🗺️ Game Roadmap",
                description="A live snapshot of the current development plans.",
                color=0x5865F2,
            )

            for s in VALID_STATUSES:
                items = [(i, d) for i, d in roadmap_items.items() if d["status"] == s]
                if items:
                    lines = [f"`#{i}` {d['title']}" for i, d in items]
                    embed.add_field(
                        name=f"{STATUS_ICONS[s]} {s.replace('-', ' ').title()} ({len(items)})",
                        value="\n".join(lines),
                        inline=False,
                    )

            if len(embed.fields) == 0:
                embed.description = "The roadmap is currently empty. Staff can add items with `/roadmap add`."

            embed.set_footer(text="CFrame Bot · Roadmap | Staff: /roadmap add • update • remove")
            await interaction.response.send_message(embed=embed)

        # ── add ───────────────────────────────────────────────────────────────
        elif action == "add":
            if not is_staff:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            if not title:
                await interaction.response.send_message("❌ Provide a `title`.", ephemeral=True)
                return
            status = (status or "planned").lower()
            if status not in VALID_STATUSES:
                await interaction.response.send_message(
                    f"❌ Invalid status. Use: `{'`, `'.join(VALID_STATUSES)}`", ephemeral=True)
                return

            roadmap_counter += 1
            roadmap_items[roadmap_counter] = {
                "title":    title,
                "status":   status,
                "added_by": str(interaction.user),
            }

            embed = discord.Embed(
                title       = f"{STATUS_ICONS[status]} Added to Roadmap",
                description = f"**#{roadmap_counter} — {title}**\nStatus: `{status}`",
                color       = STATUS_COLORS[status],
            )
            embed.set_footer(text="CFrame Bot · Roadmap")
            await interaction.response.send_message(embed=embed)
            await send_log(self.bot, "COMMAND",
                f"{interaction.user} added roadmap item **#{roadmap_counter}**: `{title}` (`{status}`)")

        # ── update ────────────────────────────────────────────────────────────
        elif action == "update":
            if not is_staff:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            if not item_id or not status:
                await interaction.response.send_message("❌ Provide both `item_id` and `status`.", ephemeral=True)
                return
            if item_id not in roadmap_items:
                await interaction.response.send_message(f"❌ No item found with ID `#{item_id}`.", ephemeral=True)
                return
            status = status.lower()
            if status not in VALID_STATUSES:
                await interaction.response.send_message(
                    f"❌ Invalid status. Use: `{'`, `'.join(VALID_STATUSES)}`", ephemeral=True)
                return

            old_status = roadmap_items[item_id]["status"]
            roadmap_items[item_id]["status"] = status
            item_title = roadmap_items[item_id]["title"]

            embed = discord.Embed(
                title       = f"🔄 Roadmap Updated",
                description = (
                    f"**#{item_id} — {item_title}**\n"
                    f"{STATUS_ICONS[old_status]} `{old_status}` → {STATUS_ICONS[status]} `{status}`"
                ),
                color = STATUS_COLORS[status],
            )
            embed.set_footer(text="CFrame Bot · Roadmap")
            await interaction.response.send_message(embed=embed)
            await send_log(self.bot, "COMMAND",
                f"{interaction.user} updated roadmap **#{item_id}** `{item_title}`: `{old_status}` → `{status}`")

        # ── remove ────────────────────────────────────────────────────────────
        elif action == "remove":
            if not is_staff:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            if not item_id:
                await interaction.response.send_message("❌ Provide an `item_id`.", ephemeral=True)
                return
            if item_id not in roadmap_items:
                await interaction.response.send_message(f"❌ No item found with ID `#{item_id}`.", ephemeral=True)
                return

            removed = roadmap_items.pop(item_id)
            await interaction.response.send_message(
                f"🗑️ Removed **#{item_id} — {removed['title']}** from the roadmap.", ephemeral=False)
            await send_log(self.bot, "COMMAND",
                f"{interaction.user} removed roadmap item **#{item_id}**: `{removed['title']}`")

        # ── clear ─────────────────────────────────────────────────────────────
        elif action == "clear":
            if interaction.user.id != DEV_ID:
                await interaction.response.send_message("❌ Owner only.", ephemeral=True)
                return
            count = len(roadmap_items)
            roadmap_items.clear()
            await interaction.response.send_message(f"🧹 Cleared **{count}** roadmap items.", ephemeral=True)
            await send_log(self.bot, "STATUS", f"Dev cleared the entire roadmap ({count} items).")

        else:
            await interaction.response.send_message(
                "❌ Invalid action. Use `view`, `add`, `update`, `remove`, or `clear`.", ephemeral=True)

    # ── /8ball ────────────────────────────────────────────────────────────────
    _8BALL_RESPONSES = [
        ("It is certain.", 0x57F287), ("It is decidedly so.", 0x57F287),
        ("Without a doubt.", 0x57F287), ("Yes, definitely.", 0x57F287),
        ("You may rely on it.", 0x57F287), ("As I see it, yes.", 0x57F287),
        ("Most likely.", 0x57F287), ("Outlook good.", 0x57F287),
        ("Signs point to yes.", 0x57F287),
        ("Reply hazy, try again.", 0xFEE75C), ("Ask again later.", 0xFEE75C),
        ("Better not tell you now.", 0xFEE75C), ("Cannot predict now.", 0xFEE75C),
        ("Concentrate and ask again.", 0xFEE75C),
        ("Don't count on it.", 0xED4245), ("My reply is no.", 0xED4245),
        ("My sources say no.", 0xED4245), ("Outlook not so good.", 0xED4245),
        ("Very doubtful.", 0xED4245),
    ]

    @discord.app_commands.command(name="8ball", description="Ask the magic 8 ball a question")
    @discord.app_commands.describe(question="Your yes/no question")
    async def eightball(self, interaction: discord.Interaction, question: str):
        if await blocked(interaction): return
        answer, color = random.choice(self._8BALL_RESPONSES)
        embed = discord.Embed(color=color)
        embed.set_author(name="Magic 8-Ball", icon_url="https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Magic_eight_ball.png/240px-Magic_eight_ball.png")
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=f"🎱  {answer}", inline=False)
        embed.set_footer(text=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /poll ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="poll", description="Create a poll with up to 4 options — optionally auto-closes")
    @discord.app_commands.describe(
        question="The poll question",
        option1="First option", option2="Second option",
        option3="Third option (optional)", option4="Fourth option (optional)",
        duration="Auto-close after this long, e.g. 10m, 1h, 2d (omit for permanent poll)",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        duration: str = None,
    ):
        if await blocked(interaction): return
        options = [o for o in [option1, option2, option3, option4] if o]
        emojis = ["🇦", "🇧", "🇨", "🇩"]
        lines = [f"{emojis[i]}  {opt}" for i, opt in enumerate(options)]

        # Parse optional duration
        seconds = None
        duration_label = None
        if duration:
            match = re.fullmatch(r"(\d+)(s|m|h|d)", duration.strip().lower())
            if not match:
                await interaction.response.send_message("❌ Invalid duration format. Use `10m`, `2h`, `1d`, etc.", ephemeral=True)
                return
            amount, unit = int(match.group(1)), match.group(2)
            seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
            if seconds > 86400 * 7:
                await interaction.response.send_message("❌ Max poll duration is 7 days.", ephemeral=True)
                return
            unit_names = {"s": "second", "m": "minute", "h": "hour", "d": "day"}
            duration_label = f"{amount} {unit_names[unit]}{'s' if amount != 1 else ''}"

        embed = discord.Embed(
            title=f"📊  {question}",
            description="\n\n".join(lines),
            color=0x5865F2,
        )
        footer_text = f"Poll by {interaction.user.display_name}"
        if duration_label:
            footer_text += f"  ·  Closes in {duration_label}"
        embed.set_footer(text=footer_text, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(options)):
            await msg.add_reaction(emojis[i])

        if seconds:
            asyncio.create_task(self._close_poll(msg, question, options, emojis, seconds, interaction.channel_id))

    async def _close_poll(
        self,
        msg: discord.Message,
        question: str,
        options: list[str],
        emojis: list[str],
        seconds: int,
        channel_id: int,
    ):
        await asyncio.sleep(seconds)
        try:
            msg = await msg.channel.fetch_message(msg.id)
        except Exception:
            return

        # Count votes (subtract 1 for the bot's own reaction)
        reaction_map = {str(r.emoji): max(0, r.count - 1) for r in msg.reactions}
        total = sum(reaction_map.get(emojis[i], 0) for i in range(len(options)))

        lines = []
        for i, opt in enumerate(options):
            votes = reaction_map.get(emojis[i], 0)
            pct = round(votes / total * 100) if total > 0 else 0
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"{emojis[i]}  **{opt}**\n`{bar}` {votes} vote{'s' if votes != 1 else ''} ({pct}%)")

        results_embed = discord.Embed(
            title=f"📊 Poll Closed — {question}",
            description="\n\n".join(lines),
            color=0x57F287 if total > 0 else 0x99AAB5,
        )
        results_embed.set_footer(text=f"Total votes: {total}")

        # Edit original to show it's closed
        try:
            closed_embed = discord.Embed(
                title=f"📊  {question}",
                description="\n\n".join(
                    f"{emojis[i]}  {opt}" for i, opt in enumerate(options)
                ) + "\n\n*⏰ This poll is now closed.*",
                color=0x99AAB5,
            )
            closed_embed.set_footer(text="Poll closed")
            await msg.edit(embed=closed_embed)
        except Exception:
            pass

        try:
            await msg.reply(embed=results_embed)
        except Exception:
            pass

    # ── /remind ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="remind", description="Set a reminder — the bot will DM you")
    @discord.app_commands.describe(time="Duration, e.g. 10m, 2h, 1d", message="What to remind you about")
    async def remind(self, interaction: discord.Interaction, time: str, message: str):
        if await blocked(interaction): return
        # Parse duration
        match = re.fullmatch(r"(\d+)(s|m|h|d)", time.strip().lower())
        if not match:
            await interaction.response.send_message("❌ Invalid format. Use `10m`, `2h`, `1d`, etc.", ephemeral=True)
            return
        amount, unit = int(match.group(1)), match.group(2)
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * amount
        if seconds > 86400 * 7:
            await interaction.response.send_message("❌ Max reminder time is 7 days.", ephemeral=True)
            return
        unit_names = {"s": "second", "m": "minute", "h": "hour", "d": "day"}
        label = f"{amount} {unit_names[unit]}{'s' if amount != 1 else ''}"
        embed = discord.Embed(description=f"⏰  I'll remind you in **{label}**.", color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        async def _send_reminder():
            await asyncio.sleep(seconds)
            try:
                remind_embed = discord.Embed(
                    title="⏰  Reminder",
                    description=message,
                    color=0x5865F2,
                    timestamp=datetime.now(timezone.utc),
                )
                remind_embed.set_footer(text=f"Set {label} ago")
                await interaction.user.send(embed=remind_embed)
            except Exception:
                pass

        asyncio.create_task(_send_reminder())

    # ── /userinfo ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="userinfo", description="Show detailed info about a user")
    @discord.app_commands.describe(user="User to inspect (defaults to yourself)")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        if await blocked(interaction): return
        member = user or interaction.user
        created_days = (datetime.now(timezone.utc) - member.created_at).days
        joined_days = (datetime.now(timezone.utc) - member.joined_at).days if member.joined_at else 0
        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        roles_str = " ".join(roles[:10]) + (f" +{len(roles)-10} more" if len(roles) > 10 else "") if roles else "None"
        embed = discord.Embed(color=member.color if member.color.value else 0x5865F2)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Nickname", value=member.display_name, inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:D>\n{created_days:,} days ago", inline=True)
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:D>\n{joined_days:,} days ago" if member.joined_at else "Unknown", inline=True)
        embed.add_field(name=f"Roles [{len(roles)}]", value=roles_str, inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /serverinfo ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="serverinfo", description="Show server stats and info")
    async def serverinfo(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        g = interaction.guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        created_days = (datetime.now(timezone.utc) - g.created_at).days
        embed = discord.Embed(title=g.name, color=0x5865F2)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(name="ID", value=str(g.id), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:D>\n{created_days:,} days ago", inline=True)
        embed.add_field(name="Members", value=f"{humans:,} humans\n{bots:,} bots", inline=True)
        embed.add_field(name="Channels", value=f"{len(g.text_channels)} text\n{len(g.voice_channels)} voice", inline=True)
        embed.add_field(name="Boosts", value=f"Level {g.premium_tier}  ·  {g.premium_subscription_count} boosts", inline=True)
        embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
        embed.add_field(name="Emojis", value=str(len(g.emojis)), inline=True)
        embed.add_field(name="Verification", value=str(g.verification_level).capitalize(), inline=True)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /avatar ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="avatar", description="Show a user's avatar in full size")
    @discord.app_commands.describe(user="User whose avatar to show")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member = None):
        if await blocked(interaction): return
        member = user or interaction.user
        av = member.display_avatar.with_size(1024)
        embed = discord.Embed(color=0x2b2d31)
        embed.set_author(name=member.display_name, icon_url=av.url)
        embed.set_image(url=av.url)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open in browser", url=str(av.url), style=discord.ButtonStyle.link))
        await interaction.response.send_message(embed=embed, view=view)

    # ── /roast & /compliment ──────────────────────────────────────────────────
    @discord.app_commands.command(name="roast", description="Get the AI to roast someone (all in good fun)")
    @discord.app_commands.describe(user="Who to roast")
    async def roast(self, interaction: discord.Interaction, user: discord.Member):
        if await blocked(interaction): return
        await interaction.response.defer()
        try:
            resp = await ai_client.chat.completions.create(
                model="llama-3.1-8b-instant", max_tokens=150,
                messages=[
                    {"role": "system", "content": "You are a comedic roast bot. Write one short, clever, witty roast (2-3 sentences max). Keep it funny and PG-13 — no slurs, no genuinely hurtful content."},
                    {"role": "user", "content": f"Roast a Discord user named {user.display_name}."},
                ]
            )
            roast_text = resp.choices[0].message.content.strip()
        except Exception:
            roast_text = f"{user.display_name}'s code doesn't even compile."
        embed = discord.Embed(description=roast_text, color=0xED4245)
        embed.set_author(name=f"🔥  Roasting {user.display_name}", icon_url=user.display_avatar.url)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @discord.app_commands.command(name="compliment", description="Send someone a genuine compliment")
    @discord.app_commands.describe(user="Who to compliment")
    async def compliment(self, interaction: discord.Interaction, user: discord.Member):
        if await blocked(interaction): return
        await interaction.response.defer()
        try:
            resp = await ai_client.chat.completions.create(
                model="llama-3.1-8b-instant", max_tokens=120,
                messages=[
                    {"role": "system", "content": "You write short, genuine, heartfelt compliments (2-3 sentences). Be warm and creative."},
                    {"role": "user", "content": f"Write a compliment for a Discord user named {user.display_name}."},
                ]
            )
            text = resp.choices[0].message.content.strip()
        except Exception:
            text = f"{user.display_name} brings so much positive energy — the server is genuinely better with them in it."
        embed = discord.Embed(description=text, color=0x57F287)
        embed.set_author(name=f"💚  Complimenting {user.display_name}", icon_url=user.display_avatar.url)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)