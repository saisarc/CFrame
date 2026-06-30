import os
import discord
from groq import AsyncGroq
from discord.ext import commands
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