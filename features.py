import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from commands import blocked, disabled_cmds, send_log
from persistence_mongo import MongoPersistence


# ---- local fallback (optional) ----
# The bot historically used bot_data.json.
# With MongoDB enabled, we keep this state in-memory and persist only selected parts.


def _default_state():
    return {
        "guild_settings": {},
        "xp_data": {},
        "warnings": {},
        "giveaways": {},
        "anti_spam": {},
        "reaction_roles": {},
    }


state = _default_state()


# If Mongo is not configured, we still allow local JSON persistence.
DATA_FILE = os.path.join(os.path.dirname(__file__), "bot_data.json")


def _load_state_file():
    global state
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            state = loaded if isinstance(loaded, dict) else _default_state()
        except Exception:
            state = _default_state()
    else:
        state = _default_state()

    for k in ("guild_settings", "xp_data", "warnings", "giveaways", "anti_spam", "reaction_roles"):
        state.setdefault(k, {})

# MongoPersistence loads/saves only supported subsets.
# We intentionally do NOT persist chat history, and we only persist xp_state if you later enable it.



def _save_state_file():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


_load_state_file()


mongo = MongoPersistence()


async def _maybe_sync_state(kind: str, guild_id: int, payload):


    """Write selected state to MongoDB.

    kind: one of guild_settings|warnings|giveaways|reaction_roles
    """
    # If Mongo isn't configured, just keep legacy local JSON behavior.
    try:
        if not os.getenv("MONGODB_URI"):
            return
        if kind == "guild_settings":
            await mongo.set_guild_settings(guild_id, payload)
        elif kind == "warnings":
            await mongo.set_warnings(guild_id, payload)
        elif kind == "giveaways":
            await mongo.set_giveaways(guild_id, payload)
        elif kind == "reaction_roles":
            await mongo.set_reaction_roles(guild_id, payload)
    except Exception:
        # Avoid crashing moderation features if DB is temporarily down.
        return


async def load_guild_settings(guild_id: int):
    """Load guild settings from Mongo when configured; otherwise use in-memory/local fallback."""
    default_settings = {
        "log_channel_id": None,
        "welcome_enabled": False,
        "welcome_channel_id": None,
        "welcome_message": "Welcome {mention} to {server}! We now have {member_count} members.",
        "leveling_enabled": True,
        "anti_spam_enabled": False,
        "anti_spam_threshold": 5,
        "anti_spam_window": 10,
        "auto_role_ids": [],
    }

    gid = str(guild_id)

    # Mongo path
    if os.getenv("MONGODB_URI"):
        try:
            # MongoPersistence returns dict
            settings = await mongo.get_guild_settings(guild_id)
            merged = default_settings | (settings or {})
            # keep in-memory copy so existing handlers can read synchronously
            state["guild_settings"][gid] = merged
            return merged
        except Exception:
            # fall back to whatever we have in memory
            pass

    # Fallback path
    settings = state["guild_settings"].setdefault(gid, default_settings.copy())
    return settings


async def save_guild_settings(guild_id: int, settings: dict):
    """Persist guild settings to Mongo when configured; always update in-memory cache."""
    gid = str(guild_id)
    state["guild_settings"][gid] = settings

    if os.getenv("MONGODB_URI"):
        try:
            await mongo.set_guild_settings(guild_id, settings)
            print(
                "[mongo] saved guild_settings "
                f"guild_id={guild_id} "
                f"log_channel_id={settings.get('log_channel_id')} "
                f"welcome_channel_id={settings.get('welcome_channel_id')} "
                f"welcome_enabled={settings.get('welcome_enabled')}"
            )
        except Exception:
            # avoid crashing moderation features if DB is temporarily down
            print(f"[mongo] failed to save guild_settings guild_id={guild_id}")
            return


def save_state():
    """Persist to Mongo/local file.

    Chat history and XP leveling are intentionally NOT persisted.

    Note: moderation persistence is handled by save_guild_settings()/Mongo, not this function.
    """
    if not os.getenv("MONGODB_URI"):
        _save_state_file()


def get_guild_settings(guild_id):
    """Synchronous getter for current cached guild settings.

    If Mongo is enabled and the cache is empty, call load_guild_settings(guild_id)
    before using this.
    """
    gid = str(guild_id)
    return state["guild_settings"].setdefault(
        gid,
        {
            "log_channel_id": None,
            "welcome_enabled": False,
            "welcome_channel_id": None,
            "welcome_message": "Welcome {mention} to {server}! We now have {member_count} members.",
            "leveling_enabled": True,
            "anti_spam_enabled": False,
            "anti_spam_threshold": 5,
            "anti_spam_window": 10,
            "auto_role_ids": [],
        },
    )



def get_user_xp(guild_id, user_id):

    gid = str(guild_id)
    uid = str(user_id)
    guild_xp = state["xp_data"].setdefault(gid, {})
    user_data = guild_xp.setdefault(uid, {"xp": 0, "level": 1, "last_message": 0})
    return user_data


def xp_for_level(level):
    return 100 + (level - 1) * 45


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    hours, rem = divmod(rem, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if rem or not parts:
        parts.append(f"{rem}s")
    return " ".join(parts)


def parse_duration(value):
    match = re.fullmatch(r"(\d+)([smhd])", value.strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


class Features(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.level_task.start()
        self.giveaway_task.start()
        self.birthday_task.start()

        # birthdays: { guild_id: { user_id: "MM-DD" } }
        self._birthday_file = os.path.join(os.path.dirname(__file__), "birthdays.json")
        self._birthdays: dict[str, dict[str, str]] = self._load_birthdays()

    def _load_birthdays(self) -> dict:
        if os.path.exists(self._birthday_file):
            try:
                with open(self._birthday_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_birthdays(self):
        try:
            with open(self._birthday_file, "w") as f:
                json.dump(self._birthdays, f, indent=2)
        except Exception:
            pass

    def cog_unload(self):
        self.level_task.cancel()
        self.giveaway_task.cancel()
        self.birthday_task.cancel()

    @tasks.loop(minutes=1)
    async def giveaway_task(self):
        now = time.time()
        for guild_id, giveaways in list(state["giveaways"].items()):
            for giveaway in list(giveaways):
                if giveaway.get("ended"):
                    continue
                if giveaway.get("ends_at", 0) <= now:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        channel = guild.get_channel(int(giveaway["channel_id"]))
                        if channel:
                            participants = giveaway.get("participants", [])
                            winner_count = max(1, int(giveaway.get("winner_count", 1)))
                            if participants:
                                winners = random.sample(participants, k=min(winner_count, len(participants)))
                                winner_mentions = [f"<@{uid}>" for uid in winners]
                                message = f"🎉 Giveaway ended! Winners: {', '.join(winner_mentions)}"
                            else:
                                message = "🎉 Giveaway ended! No valid participants joined."
                            embed = discord.Embed(
                                title="🎉 Giveaway Ended",
                                description=f"**Prize:** {giveaway['prize']}\n{message}",
                                color=0x57F287,
                            )
                            embed.set_footer(text="CFrame Bot · Giveaway")
                            await channel.send(embed=embed)
                    giveaway["ended"] = True
                    giveaway["winner_ids"] = giveaway.get("participants", [])
                    # Mongo disabled: keep giveaways in-memory + local file only.
                    save_state()


    @giveaway_task.before_loop
    async def before_giveaway(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def level_task(self):
        save_state()

    @level_task.before_loop
    async def before_level_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def birthday_task(self):
        now = datetime.now(timezone.utc)
        today = now.strftime("%m-%d")
        for guild_id_str, users in self._birthdays.items():
            guild = self.bot.get_guild(int(guild_id_str))
            if not guild:
                continue
            settings = get_guild_settings(int(guild_id_str))
            ch_id = settings.get("welcome_channel_id") or settings.get("log_channel_id")
            if not ch_id:
                continue
            channel = guild.get_channel(int(ch_id))
            if not channel:
                continue
            for user_id_str, bday in users.items():
                if bday == today:
                    member = guild.get_member(int(user_id_str))
                    if not member:
                        continue
                    embed = discord.Embed(
                        title="🎂  Happy Birthday!",
                        description=f"Wishing {member.mention} a wonderful birthday! 🎉",
                        color=0xFEE75C,
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else discord.Embed.Empty)
                    try:
                        await channel.send(embed=embed)
                    except Exception:
                        pass

    @birthday_task.before_loop
    async def before_birthday_task(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(name="birthday", description="Set or view birthdays")
    @discord.app_commands.describe(action="set or list", date="Your birthday in MM-DD format (e.g. 07-04)", user="User to look up (for list)")
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="set", value="set"),
        discord.app_commands.Choice(name="list", value="list"),
    ])
    async def birthday(self, interaction: discord.Interaction, action: str, date: str = None, user: discord.Member = None):
        if await blocked(interaction): return
        guild_id = str(interaction.guild.id)
        self._birthdays.setdefault(guild_id, {})

        if action == "set":
            if not date:
                await interaction.response.send_message("❌ Provide a date in MM-DD format.", ephemeral=True)
                return
            if not re.fullmatch(r"\d{2}-\d{2}", date):
                await interaction.response.send_message("❌ Format must be MM-DD, e.g. `07-04`.", ephemeral=True)
                return
            self._birthdays[guild_id][str(interaction.user.id)] = date
            self._save_birthdays()
            embed = discord.Embed(description=f"🎂  Birthday set to **{date}**.", color=0xFEE75C)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif action == "list":
            entries = self._birthdays.get(guild_id, {})
            if not entries:
                await interaction.response.send_message("No birthdays registered yet.", ephemeral=True)
                return
            lines = []
            for uid, bday in sorted(entries.items(), key=lambda x: x[1]):
                m = interaction.guild.get_member(int(uid))
                name = m.display_name if m else f"<@{uid}>"
                lines.append(f"**{name}** — {bday}")
            embed = discord.Embed(title="🎂  Server Birthdays", description="\n".join(lines), color=0xFEE75C)
            embed.set_footer(text=f"{len(lines)} registered")
            await interaction.response.send_message(embed=embed)

    async def _send_log(self, guild_id, title, description, color=0x5865F2):
        # Ensure cache is populated from Mongo (if enabled) before reading log_channel_id
        if os.getenv("MONGODB_URI"):
            await load_guild_settings(int(guild_id))
        settings = get_guild_settings(guild_id)

        log_channel_id = settings.get("log_channel_id")

        if not log_channel_id:
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return
        channel = guild.get_channel(int(log_channel_id))
        if not channel:
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text="CFrame Bot · Logs")
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Always load fresh from MongoDB so settings survive redeployments
        settings = await load_guild_settings(member.guild.id)
        if settings.get("auto_role_ids"):
            for role_id in settings.get("auto_role_ids", []):
                role = member.guild.get_role(int(role_id))
                if role and role < member.guild.me.top_role:
                    try:
                        await member.add_roles(role, reason="Auto-role")
                    except Exception:
                        pass

        if not settings.get("welcome_enabled"):
            return
        channel_id = settings.get("welcome_channel_id")
        if not channel_id:
            return
        channel = member.guild.get_channel(int(channel_id))
        if not channel:
            return

        # Build a rich welcome embed
        bot_name = member.guild.me.display_name if member.guild.me else "CFrame"
        bot_icon = member.guild.me.display_avatar.url if member.guild.me else None

        embed = discord.Embed(color=0x57F287)
        embed.set_author(name=f"Welcome to {member.guild.name}!")
        embed.description = (
            f"### {member.display_name}\n"
            f"**@{member.name}**\n\n"
            f"Glad to have you here! You're member **#{member.guild.member_count:,}**."
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.guild.banner:
            embed.set_image(url=member.guild.banner.url)
        embed.set_footer(
            text=bot_name,
            icon_url=bot_icon,
        )
        try:
            await channel.send(content=member.mention, embed=embed)
        except Exception:
            pass
        await self._send_log(member.guild.id, "👋 Member Joined", f"{member.mention} joined the server.", 0x57F287)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_log(member.guild.id, "👋 Member Left", f"{member.mention} left the server.", 0xFEE75C)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        await self._send_log(
            message.guild.id,
            "🗑️ Message Deleted",
            f"**Author:** {message.author.mention}\n**Channel:** {message.channel.mention}\n**Content:** {message.content[:1000] or '*empty*'}",
            0xFF6B6B,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or before.content == after.content or after.author.bot:
            return
        await self._send_log(
            after.guild.id,
            "✏️ Message Edited",
            f"**Author:** {after.author.mention}\n**Channel:** {after.channel.mention}\n**Before:** {before.content[:500] or '*empty*'}\n**After:** {after.content[:500] or '*empty*'}",
            0xFEE75C,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick == after.nick:
            if set(before.roles) == set(after.roles):
                return
        changes = []
        if before.nick != after.nick:
            changes.append(f"Nickname: `{before.nick or before.display_name}` → `{after.nick or after.display_name}`")
        if set(before.roles) != set(after.roles):
            role_names = []
            for role in after.roles:
                if role.name != "@everyone":
                    role_names.append(role.name)
            changes.append(f"Roles: {', '.join(role_names) or 'None'}")
        if changes:
            await self._send_log(after.guild.id, "👤 Member Updated", f"{after.mention}\n" + "\n".join(changes), 0x5865F2)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._send_log(guild.id, "🔨 Member Banned", f"{user.mention} was banned.", 0xFF6B6B)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self._send_log(guild.id, "🔓 Member Unbanned", f"{user.mention} was unbanned.", 0x57F287)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        settings = get_guild_settings(message.guild.id)
        if settings.get("anti_spam_enabled", False):
            spam_state = state["anti_spam"].setdefault(str(message.guild.id), {})
            user_spam = spam_state.setdefault(str(message.author.id), [])
            now = time.time()
            window = max(5, int(settings.get("anti_spam_window", 10)))
            user_spam[:] = [stamp for stamp in user_spam if now - stamp <= window]
            user_spam.append(now)
            threshold = max(2, int(settings.get("anti_spam_threshold", 5)))
            if len(user_spam) >= threshold:
                try:
                    await message.delete()
                except Exception:
                    pass
                try:
                    await message.author.timeout_for(timedelta(minutes=1), reason="Anti-spam protection")
                except Exception:
                    pass
                await self._send_log(message.guild.id, "🚨 Spam Detected", f"{message.author.mention} triggered anti-spam protection.", 0xFF6B6B)
                user_spam.clear()
                return

        if not settings.get("leveling_enabled", True):
            return
        user_data = get_user_xp(message.guild.id, message.author.id)
        now = time.time()
        if now - user_data["last_message"] < 45:
            return
        user_data["last_message"] = now
        base_gain = 5 + min(12, len(message.content.split()) // 2)
        user_data["xp"] += base_gain
        while user_data["xp"] >= xp_for_level(user_data["level"]):
            user_data["xp"] -= xp_for_level(user_data["level"])
            user_data["level"] += 1
        save_state()

    # ── /moderation — NEW Advanced overview & settings hub ─────────────────
    @discord.app_commands.command(
        name="moderation",
        description="Advanced moderation & server settings (overview / configure)"
    )
    @discord.app_commands.describe(
        action="overview, logging, welcome, antispam, leveling"
    )
    async def moderation(self, interaction: discord.Interaction, action: str):
        if await blocked(interaction):
            return

        action = (action or "overview").lower()
        settings = get_guild_settings(interaction.guild_id)

        if action == "overview":
            log_channel = interaction.guild.get_channel(int(settings["log_channel_id"])) if settings.get("log_channel_id") else None
            welcome_channel = interaction.guild.get_channel(int(settings["welcome_channel_id"])) if settings.get("welcome_channel_id") else None

            embed = discord.Embed(title="🛡️ Moderation Overview", color=0x5865F2)
            embed.add_field(name="Logging", value=log_channel.mention if log_channel else "Not set", inline=False)
            embed.add_field(name="Welcome", value=("Enabled" if settings.get("welcome_enabled") else "Disabled") + (f" • {welcome_channel.mention}" if settings.get("welcome_channel_id") and welcome_channel else ""), inline=False)
            embed.add_field(name="Anti-Spam", value=("Enabled" if settings.get("anti_spam_enabled") else "Disabled") + f" • threshold {settings.get('anti_spam_threshold', 5)}", inline=False)
            embed.add_field(name="Leveling", value="Enabled" if settings.get("leveling_enabled") else "Disabled", inline=False)
            embed.add_field(name="Auto Roles", value=str(len(settings.get("auto_role_ids", []))) if settings.get("auto_role_ids") else "None", inline=False)
            embed.set_footer(text="CFrame Bot · Moderation")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # LOGGING
        if action == "logging":
            log_channel = interaction.guild.get_channel(int(settings["log_channel_id"])) if settings.get("log_channel_id") else None
            embed = discord.Embed(title="🧾 Logging Settings", color=0x5865F2)
            embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "Not set", inline=False)
            embed.add_field(name="Use", value="`/setlogchannel` or `/clearlogchannel`", inline=False)
            embed.set_footer(text="CFrame Bot · Moderation")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # WELCOME
        if action == "welcome":
            welcome_channel = interaction.guild.get_channel(int(settings["welcome_channel_id"])) if settings.get("welcome_channel_id") else None
            embed = discord.Embed(title="👋 Welcome Settings", color=0x5865F2)
            embed.add_field(name="Enabled", value="Yes" if settings.get("welcome_enabled") else "No", inline=True)
            embed.add_field(name="Channel", value=welcome_channel.mention if welcome_channel else "Not set", inline=True)
            embed.add_field(name="Message", value=settings.get("welcome_message", "Not set")[:500], inline=False)
            embed.set_footer(text="CFrame Bot · Moderation")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ANTI-SPAM
        if action == "antispam":
            embed = discord.Embed(title="🚨 Anti-Spam Settings", color=0x5865F2)
            embed.add_field(name="Enabled", value="Yes" if settings.get("anti_spam_enabled") else "No", inline=True)
            embed.add_field(name="Threshold", value=str(settings.get("anti_spam_threshold", 5)), inline=True)
            embed.set_footer(text="CFrame Bot · Moderation")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # LEVELING
        if action == "leveling":
            embed = discord.Embed(title="📈 Leveling Settings", color=0x5865F2)
            embed.add_field(name="Enabled", value="Yes" if settings.get("leveling_enabled") else "No", inline=True)
            embed.set_footer(text="CFrame Bot · Moderation")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.send_message("❌ Unknown action. Use: overview, logging, welcome, antispam, leveling", ephemeral=True)

    # ── Backwards-compatible commands (kept) ────────────────────────────────
    # /modhelp and /modsettings are deprecated; use /moderation instead.

    @discord.app_commands.command(name="modhelp", description="(Deprecated) Show moderation and server management commands")
    async def modhelp(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        await interaction.response.send_message("Use `/moderation overview` for the new advanced hub.", ephemeral=True)

    @discord.app_commands.command(name="modsettings", description="(Deprecated) Show the current moderation and welcome settings")
    async def modsettings(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        await interaction.response.send_message("Use `/moderation overview` for the new advanced hub.", ephemeral=True)


    @discord.app_commands.command(name="setlogchannel", description="Set the channel where moderation logs will be sent")
    @discord.app_commands.describe(channel="The channel to use for logs")
    async def setlogchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["log_channel_id"] = channel.id
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send(f"✅ Log channel set to {channel.mention}.", ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} set the log channel to {channel.mention}.")

    @discord.app_commands.command(name="clearlogchannel", description="Disable moderation logs for this server")
    async def clearlogchannel(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["log_channel_id"] = None
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send("✅ Logging has been disabled for this server.", ephemeral=True)

    @discord.app_commands.command(name="setwelcomechannel", description="Set the welcome message channel")
    @discord.app_commands.describe(channel="The channel where new members should be welcomed")
    async def setwelcomechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["welcome_channel_id"] = channel.id
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send(f"✅ Welcome channel set to {channel.mention}.", ephemeral=True)

    @discord.app_commands.command(name="setwelcomemessage", description="Set the welcome message shown to new members")
    @discord.app_commands.describe(message="The welcome message template")
    async def setwelcomemessage(self, interaction: discord.Interaction, message: str):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["welcome_message"] = message
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send("✅ Welcome message updated.", ephemeral=True)

    @discord.app_commands.command(name="disablewelcome", description="Turn welcome messages on or off")
    @discord.app_commands.describe(enabled="Whether welcome messages should be enabled")
    async def disablewelcome(self, interaction: discord.Interaction, enabled: bool):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["welcome_enabled"] = enabled
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send(f"✅ Welcome messages {'enabled' if enabled else 'disabled'}.", ephemeral=True)

    @discord.app_commands.command(name="welcometest", description="Send a test welcome message")
    async def welcometest(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        settings = get_guild_settings(interaction.guild_id)
        if not settings.get("welcome_enabled"):
            await interaction.response.send_message("⚠️ Welcome messages are currently disabled.", ephemeral=True)
            return
        channel_id = settings.get("welcome_channel_id")
        if not channel_id:
            await interaction.response.send_message("⚠️ No welcome channel has been set.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(int(channel_id))
        if not channel:
            await interaction.response.send_message("⚠️ That channel could not be found.", ephemeral=True)
            return
        message = settings.get("welcome_message", "Welcome {mention} to {server}!").format(
            mention=interaction.user.mention,
            user=interaction.user.name,
            server=interaction.guild.name,
            member_count=interaction.guild.member_count,
            user_name=interaction.user.display_name,
        )
        await interaction.response.send_message(f"✅ Welcome test sent to {channel.mention}.", ephemeral=True)
        await channel.send(message)

    @discord.app_commands.command(name="warn", description="Warn a member")
    @discord.app_commands.describe(member="The member to warn", reason="The reason for the warning")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        gid = str(interaction.guild_id)
        warnings = state["warnings"].setdefault(gid, {})
        user_warnings = warnings.setdefault(str(member.id), [])
        user_warnings.append({"reason": reason, "moderator": str(interaction.user), "time": time.time()})
        save_state()
        await interaction.response.send_message(f"⚠️ {member.mention} has been warned. Reason: {reason}", ephemeral=True)
        await self._send_log(interaction.guild_id, "⚠️ Member Warned", f"{member.mention} was warned by {interaction.user.mention}\nReason: {reason}", 0xFEE75C)

    @discord.app_commands.command(name="warnings", description="View a member's warnings")
    @discord.app_commands.describe(member="The member to inspect")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        user_warnings = state["warnings"].get(str(interaction.guild_id), {}).get(str(member.id), [])
        if not user_warnings:
            await interaction.response.send_message(f"✅ {member.mention} has no warnings.", ephemeral=True)
            return
        lines = []
        for idx, item in enumerate(user_warnings, 1):
            lines.append(f"{idx}. {item['reason']} — {datetime.fromtimestamp(item['time'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        embed = discord.Embed(title=f"⚠️ Warnings for {member.display_name}", description="\n".join(lines[:10]), color=0xFEE75C)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="clearwarnings", description="Clear a member's warnings")
    @discord.app_commands.describe(member="The member whose warnings should be cleared")
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        state["warnings"].setdefault(str(interaction.guild_id), {}).pop(str(member.id), None)
        save_state()
        await interaction.response.send_message(f"✅ Cleared warnings for {member.mention}.", ephemeral=True)

    @discord.app_commands.command(name="mute", description="Mute a member for a duration")
    @discord.app_commands.describe(member="The member to mute", duration="Duration like 10m or 1h", reason="Reason for the mute")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("❌ You need Moderate Members permissions.", ephemeral=True)
            return
        seconds = parse_duration(duration)
        if not seconds:
            await interaction.response.send_message("❌ Use a duration like 10m, 1h, or 2d.", ephemeral=True)
            return
        try:
            await member.timeout_for(timedelta(seconds=seconds), reason=reason)
            await interaction.response.send_message(f"🔇 Muted {member.mention} for {format_duration(seconds)}.", ephemeral=True)
            await self._send_log(interaction.guild_id, "🔇 Member Muted", f"{member.mention} was muted for {format_duration(seconds)}\nReason: {reason}", 0xFEE75C)
        except Exception as error:
            await interaction.response.send_message(f"❌ Failed to mute: {error}", ephemeral=True)

    @discord.app_commands.command(name="unmute", description="Remove a timeout from a member")
    @discord.app_commands.describe(member="The member to unmute")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("❌ You need Moderate Members permissions.", ephemeral=True)
            return
        try:
            await member.remove_timeout(reason="Unmuted by moderator")
            await interaction.response.send_message(f"🔓 Unmuted {member.mention}.", ephemeral=True)
            await self._send_log(interaction.guild_id, "🔓 Member Unmuted", f"{member.mention} was unmuted.", 0x57F287)
        except Exception as error:
            await interaction.response.send_message(f"❌ Failed to unmute: {error}", ephemeral=True)

    @discord.app_commands.command(name="kick", description="Kick a member from the server")
    @discord.app_commands.describe(member="The member to kick", reason="Reason for the kick")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("❌ You need Kick Members permissions.", ephemeral=True)
            return
        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(f"👢 Kicked {member.mention}.", ephemeral=True)
            await self._send_log(interaction.guild_id, "👢 Member Kicked", f"{member.mention} was kicked.\nReason: {reason}", 0xFF6B6B)
        except Exception as error:
            await interaction.response.send_message(f"❌ Failed to kick: {error}", ephemeral=True)

    @discord.app_commands.command(name="ban", description="Ban a member from the server")
    @discord.app_commands.describe(member="The member to ban", reason="Reason for the ban")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("❌ You need Ban Members permissions.", ephemeral=True)
            return
        try:
            await member.ban(reason=reason)
            await interaction.response.send_message(f"🔨 Banned {member.mention}.", ephemeral=True)
            await self._send_log(interaction.guild_id, "🔨 Member Banned", f"{member.mention} was banned.\nReason: {reason}", 0xFF6B6B)
        except Exception as error:
            await interaction.response.send_message(f"❌ Failed to ban: {error}", ephemeral=True)

    @discord.app_commands.command(name="unban", description="Unban a user")
    @discord.app_commands.describe(user_id="The user ID to unban")
    async def unban(self, interaction: discord.Interaction, user_id: str):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("❌ You need Ban Members permissions.", ephemeral=True)
            return
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason="Unbanned by moderator")
            await interaction.response.send_message(f"✅ Unbanned {user.mention}.", ephemeral=True)
            await self._send_log(interaction.guild_id, "🔓 Member Unbanned", f"{user.mention} was unbanned.", 0x57F287)
        except Exception as error:
            await interaction.response.send_message(f"❌ Failed to unban: {error}", ephemeral=True)

    @discord.app_commands.command(name="clear", description="Delete a number of messages")
    @discord.app_commands.describe(amount="The number of messages to delete")
    async def clear(self, interaction: discord.Interaction, amount: int):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Choose a number between 1 and 100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages.", ephemeral=True)
        await self._send_log(interaction.guild_id, "🧹 Messages Purged", f"{interaction.user.mention} deleted {len(deleted)} messages in {interaction.channel.mention}.", 0x5865F2)

    @discord.app_commands.command(name="slowmode", description="Set the channel slowmode")
    @discord.app_commands.describe(seconds="Slowmode delay in seconds")
    async def slowmode(self, interaction: discord.Interaction, seconds: int):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ You need Manage Channels permissions.", ephemeral=True)
            return
        if seconds < 0 or seconds > 21600:
            await interaction.response.send_message("❌ Choose a value between 0 and 21600 seconds.", ephemeral=True)
            return
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"⏱️ Slowmode set to {seconds} seconds.", ephemeral=True)
        await self._send_log(interaction.guild_id, "⏱️ Slowmode Updated", f"{interaction.user.mention} set slowmode to {seconds} seconds in {interaction.channel.mention}.", 0x5865F2)

    @discord.app_commands.command(name="gstart", description="Start a giveaway")
    @discord.app_commands.describe(prize="The prize for the giveaway", duration="How long it should run (e.g. 30m)", winner_count="How many winners there should be")
    async def gstart(self, interaction: discord.Interaction, prize: str, duration: str, winner_count: int = 1):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        seconds = parse_duration(duration)
        if not seconds:
            await interaction.response.send_message("❌ Use a duration like 10m, 1h, or 2d.", ephemeral=True)
            return
        ends_at = time.time() + seconds
        embed = discord.Embed(title="🎉 Giveaway Started", description=f"**Prize:** {prize}\n**Winners:** {winner_count}\n**Ends:** <t:{int(ends_at)}:F>", color=0xFEE75C)
        embed.set_footer(text="React with 🎉 to enter")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await msg.add_reaction("🎉")
        giveaway = {
            "message_id": str(msg.id),
            "channel_id": str(msg.channel.id),
            "guild_id": str(interaction.guild_id),
            "prize": prize,
            "winner_count": winner_count,
            "ends_at": ends_at,
            "participants": [],
            "ended": False,
        }
        state["giveaways"].setdefault(str(interaction.guild_id), []).append(giveaway)
        save_state()

    @discord.app_commands.command(name="gend", description="End a giveaway manually")
    @discord.app_commands.describe(message_id="The giveaway message ID")
    async def gend(self, interaction: discord.Interaction, message_id: str):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        giveaways = state["giveaways"].get(str(interaction.guild_id), [])
        giveaway = next((item for item in giveaways if str(item.get("message_id")) == message_id), None)
        if not giveaway:
            await interaction.response.send_message("❌ No giveaway found with that message ID.", ephemeral=True)
            return
        giveaway["ends_at"] = 0
        await interaction.response.send_message("✅ Giveaway end request processed.", ephemeral=True)

    @discord.app_commands.command(name="greroll", description="Reroll a finished giveaway")
    @discord.app_commands.describe(message_id="The giveaway message ID")
    async def greroll(self, interaction: discord.Interaction, message_id: str):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permissions.", ephemeral=True)
            return
        giveaways = state["giveaways"].get(str(interaction.guild_id), [])
        giveaway = next((item for item in giveaways if str(item.get("message_id")) == message_id), None)
        if not giveaway or not giveaway.get("participants"):
            await interaction.response.send_message("❌ No completed giveaway with participants was found.", ephemeral=True)
            return
        winners = random.sample(giveaway["participants"], k=min(int(giveaway.get("winner_count", 1)), len(giveaway["participants"])))
        mentions = [f"<@{uid}>" for uid in winners]
        await interaction.response.send_message(f"🎉 Reroll winners: {', '.join(mentions)}", ephemeral=True)

    @discord.app_commands.command(name="level", description="Show your current leveling progress")
    async def level(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        user_data = get_user_xp(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="📈 Your Level", color=0x57F287)
        embed.add_field(name="Level", value=str(user_data["level"]), inline=True)
        embed.add_field(name="XP", value=f"{user_data['xp']}/{xp_for_level(user_data['level'])}", inline=True)
        embed.set_footer(text="CFrame Bot · Leveling")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="leaderboard", description="Show the server leveling leaderboard")
    async def leaderboard(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        guild_xp = state["xp_data"].get(str(interaction.guild_id), {})
        ranked = sorted(guild_xp.items(), key=lambda item: (item[1].get("level", 1), item[1].get("xp", 0)), reverse=True)[:10]
        if not ranked:
            await interaction.response.send_message("📊 No XP data yet. Start chatting to build the leaderboard.", ephemeral=True)
            return
        lines = []
        for index, (user_id, data) in enumerate(ranked, 1):
            user = interaction.guild.get_member(int(user_id))
            name = user.display_name if user else f"User {user_id}"
            lines.append(f"{index}. {name} — Lv {data['level']} ({data['xp']} XP)")
        embed = discord.Embed(title="🏆 Level Leaderboard", description="\n".join(lines), color=0x5865F2)
        embed.set_footer(text="CFrame Bot · Leveling")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.app_commands.command(name="toggleleveling", description="Enable or disable leveling")
    @discord.app_commands.describe(enabled="Whether leveling should be enabled")
    async def toggleleveling(self, interaction: discord.Interaction, enabled: bool):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        settings["leveling_enabled"] = enabled
        save_state()
        await interaction.response.send_message(f"✅ Leveling {'enabled' if enabled else 'disabled'}.", ephemeral=True)

    @discord.app_commands.command(name="toggleantispam", description="Enable or disable anti-spam protection")
    @discord.app_commands.describe(enabled="Whether anti-spam should be enabled")
    async def toggleantispam(self, interaction: discord.Interaction, enabled: bool):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        settings["anti_spam_enabled"] = enabled
        save_state()
        await interaction.response.send_message(f"✅ Anti-spam {'enabled' if enabled else 'disabled'}.", ephemeral=True)

    @discord.app_commands.command(name="setantispamthreshold", description="Set the number of messages before anti-spam triggers")
    @discord.app_commands.describe(threshold="How many messages within the window should trigger anti-spam")
    async def setantispamthreshold(self, interaction: discord.Interaction, threshold: int):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need Manage Server permissions.", ephemeral=True)
            return
        if threshold < 2 or threshold > 20:
            await interaction.response.send_message("❌ Choose a threshold between 2 and 20.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        settings["anti_spam_threshold"] = threshold
        save_state()
        await interaction.response.send_message(f"✅ Anti-spam threshold set to {threshold}.", ephemeral=True)

    @discord.app_commands.command(name="autorole", description="Set or clear the auto-role for new members")
    @discord.app_commands.describe(action="set, clear, or list", role="The role to assign automatically")
    async def autorole(self, interaction: discord.Interaction, action: str, role: discord.Role = None):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You need Manage Roles permissions.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        action = action.lower()
        if action == "set":
            if not role:
                await interaction.response.send_message("❌ Please provide a role.", ephemeral=True)
                return
            settings["auto_role_ids"] = [str(role.id)]
            save_state()
            await interaction.response.send_message(f"✅ Auto-role set to {role.mention}.", ephemeral=True)
        elif action == "clear":
            settings["auto_role_ids"] = []
            save_state()
            await interaction.response.send_message("✅ Auto-role cleared.", ephemeral=True)
        elif action == "list":
            roles = [f"<@&{role_id}>" for role_id in settings.get("auto_role_ids", [])]
            await interaction.response.send_message("✅ Auto roles: " + (", ".join(roles) if roles else "None"), ephemeral=True)
        else:
            await interaction.response.send_message("❌ Use `set`, `clear`, or `list`.", ephemeral=True)

    @discord.app_commands.command(name="reactionrole", description="Attach a reaction role to a message")
    @discord.app_commands.describe(action="add, remove, or list", message_id="The message ID to attach to", emoji="The emoji to use", role="The role to assign")
    async def reactionrole(self, interaction: discord.Interaction, action: str, message_id: str = None, emoji: str = None, role: discord.Role = None):
        if await blocked(interaction):
            return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You need Manage Roles permissions.", ephemeral=True)
            return
        action = action.lower()
        guild_reaction_roles = state["reaction_roles"].setdefault(str(interaction.guild_id), {})
        if action == "add":
            if not message_id or not emoji or not role:
                await interaction.response.send_message("❌ Provide a message ID, emoji, and role.", ephemeral=True)
                return
            try:
                message = await interaction.channel.fetch_message(int(message_id))
            except Exception:
                await interaction.response.send_message("❌ I could not find that message in this channel.", ephemeral=True)
                return
            guild_reaction_roles.setdefault(str(message.id), {})[emoji] = str(role.id)
            save_state()
            try:
                await message.add_reaction(emoji)
            except Exception:
                pass
            await interaction.response.send_message(f"✅ Reaction role added for {emoji} -> {role.mention}.", ephemeral=True)
        elif action == "remove":
            if not message_id or not emoji:
                await interaction.response.send_message("❌ Provide a message ID and emoji.", ephemeral=True)
                return
            message_roles = guild_reaction_roles.get(str(message_id), {})
            if emoji in message_roles:
                message_roles.pop(emoji)
                save_state()
                await interaction.response.send_message("✅ Reaction role removed.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No reaction role found for that emoji on that message.", ephemeral=True)
        elif action == "list":
            if not message_id:
                await interaction.response.send_message("❌ Provide a message ID.", ephemeral=True)
                return
            message_roles = guild_reaction_roles.get(str(message_id), {})
            if not message_roles:
                await interaction.response.send_message("✅ No reaction roles set for that message.", ephemeral=True)
                return
            lines = [f"{emoji} -> <@&{role_id}>" for emoji, role_id in message_roles.items()]
            await interaction.response.send_message("✅ Reaction roles:\n" + "\n".join(lines), ephemeral=True)
        else:
            await interaction.response.send_message("❌ Use `add`, `remove`, or `list`.", ephemeral=True)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or not reaction.message.guild:
            return
        giveaways = state["giveaways"].get(str(reaction.message.guild.id), [])
        for giveaway in giveaways:
            if giveaway.get("ended"):
                continue
            if str(reaction.message.id) != str(giveaway.get("message_id")):
                continue
            if str(user.id) in giveaway.get("participants", []):
                continue
            giveaway.setdefault("participants", []).append(str(user.id))
            save_state()

        guild_reaction_roles = state["reaction_roles"].get(str(reaction.message.guild.id), {})
        message_roles = guild_reaction_roles.get(str(reaction.message.id), {})
        if not message_roles:
            return
        emoji = str(reaction.emoji)
        role_id = message_roles.get(emoji)
        if not role_id:
            return
        role = reaction.message.guild.get_role(int(role_id))
        if role and role < reaction.message.guild.me.top_role:
            try:
                await reaction.message.guild.get_member(user.id).add_roles(role, reason="Reaction role")
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or not reaction.message.guild:
            return
        guild_reaction_roles = state["reaction_roles"].get(str(reaction.message.guild.id), {})
        message_roles = guild_reaction_roles.get(str(reaction.message.id), {})
        if not message_roles:
            return
        emoji = str(reaction.emoji)
        role_id = message_roles.get(emoji)
        if not role_id:
            return
        role = reaction.message.guild.get_role(int(role_id))
        if role and role < reaction.message.guild.me.top_role:
            member = reaction.message.guild.get_member(user.id)
            if member:
                try:
                    await member.remove_roles(role, reason="Reaction role removed")
                except Exception:
                    pass


a = Features
