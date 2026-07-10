import os
import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

from commands import send_log, blocked
from features import load_guild_settings, save_guild_settings, get_guild_settings

load_dotenv()

DEV_ID = int(os.getenv("DEV_ID", "0"))


async def _fetch_image_bytes(url: str, max_mb: int = 8) -> bytes:
    """Download an image from an http/https URL with basic SSRF guard.

    Only accepts http/https URLs and validates that the response content-type
    is an image before returning the bytes.
    """
    url = url.strip()
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("Only http/https URLs are supported.")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status} — could not download image.")
            ct = resp.headers.get("Content-Type", "")
            if not ct.startswith("image/"):
                raise ValueError(f"URL does not point to an image (Content-Type: `{ct}`).")
            data = await resp.read()
            if len(data) > max_mb * 1024 * 1024:
                raise ValueError(f"Image exceeds {max_mb} MB limit.")
            return data


class ServerTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /setservericon ────────────────────────────────────────────────────────
    @discord.app_commands.command(name="setservericon", description="Change the server's icon")
    @discord.app_commands.describe(url="Direct image URL (PNG, JPG, or GIF for animated — animated requires Level 1)")
    async def setservericon(self, interaction: discord.Interaction, url: str):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _fetch_image_bytes(url)
            await interaction.guild.edit(icon=data, reason=f"Icon changed by {interaction.user}")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
            return
        embed = discord.Embed(description="✅ Server icon updated.", color=0x57F287)
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} changed the **server icon**.")

    # ── /setserverbanner ──────────────────────────────────────────────────────
    @discord.app_commands.command(name="setserverbanner", description="Change the server banner (requires boost level 2)")
    @discord.app_commands.describe(url="Direct image URL (PNG/JPG recommended, minimum 960×540)")
    async def setserverbanner(self, interaction: discord.Interaction, url: str):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        if interaction.guild.premium_tier < 2:
            await interaction.response.send_message(
                f"❌ Server banners require **Boost Level 2** (14 boosts). "
                f"This server is currently Level {interaction.guild.premium_tier}.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _fetch_image_bytes(url)
            await interaction.guild.edit(banner=data, reason=f"Banner changed by {interaction.user}")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
            return
        await interaction.followup.send("✅ Server banner updated.", ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} changed the **server banner**.")

    # ── /setbotavatar ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="setbotavatar", description="Change the bot's avatar (owner only)")
    @discord.app_commands.describe(url="Direct image URL (PNG or JPG, max 8 MB)")
    async def setbotavatar(self, interaction: discord.Interaction, url: str):
        if interaction.user.id != DEV_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await _fetch_image_bytes(url)
            await self.bot.user.edit(avatar=data)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
            return
        embed = discord.Embed(description="✅ Bot avatar updated.", color=0x57F287)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} changed the **bot avatar**.")

    # ── /setvanityrole ────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="setvanityrole",
        description="Auto-assign a role to users who include a keyword in their status",
    )
    @discord.app_commands.describe(
        keyword="Word or phrase to watch for (e.g. your server invite code or vanity URL)",
        role="Role to assign when the keyword is found in a user's custom status",
    )
    async def setvanityrole(self, interaction: discord.Interaction, keyword: str, role: discord.Role):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "❌ That role is above or equal to my highest role — move my role above it first.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["vanity_word"] = keyword.lower()
        settings["vanity_role_id"] = role.id
        await save_guild_settings(interaction.guild_id, settings)
        embed = discord.Embed(
            title="✅ Vanity Role Configured",
            description=(
                f"Users whose custom status contains **`{keyword}`** will automatically receive {role.mention}.\n\n"
                "⚠️ **Requires** the **Presence Intent** and **Server Members Intent** to be enabled "
                "in the [Discord Developer Portal](https://discord.com/developers/applications)."
            ),
            color=0x57F287,
        )
        embed.set_footer(text="Use /clearvanityrole to disable")
        await interaction.followup.send(embed=embed, ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} set vanity role: keyword `{keyword}` → {role.mention}")

    # ── /clearvanityrole ──────────────────────────────────────────────────────
    @discord.app_commands.command(name="clearvanityrole", description="Disable the vanity role system for this server")
    async def clearvanityrole(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings.pop("vanity_word", None)
        settings.pop("vanity_role_id", None)
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send("✅ Vanity role system disabled.", ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} disabled the vanity role system.")

    # ── presence watcher (vanity role) ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Assign or remove the vanity role based on the member's custom status."""
        if after.bot or not after.guild:
            return
        settings = get_guild_settings(after.guild.id)
        vanity_word = settings.get("vanity_word")
        vanity_role_id = settings.get("vanity_role_id")
        if not vanity_word or not vanity_role_id:
            return
        role = after.guild.get_role(int(vanity_role_id))
        if not role:
            return

        has_keyword = any(
            isinstance(act, discord.CustomActivity) and act.name and vanity_word in act.name.lower()
            for act in after.activities
        )
        has_role = role in after.roles

        try:
            if has_keyword and not has_role:
                await after.add_roles(role, reason="Vanity role: keyword detected in status")
            elif not has_keyword and has_role:
                await after.remove_roles(role, reason="Vanity role: keyword removed from status")
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── /lock ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="lock", description="Lock a channel so @everyone cannot send messages")
    @discord.app_commands.describe(
        channel="Channel to lock (defaults to current channel)",
        reason="Reason for the lock",
    )
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)
            return
        ch = channel or interaction.channel
        ow = ch.overwrites_for(interaction.guild.default_role)
        if ow.send_messages is False:
            await interaction.response.send_message(f"⚠️ {ch.mention} is already locked.", ephemeral=True)
            return
        ow.send_messages = False
        try:
            await ch.edit(overwrites={interaction.guild.default_role: ow}, reason=f"Locked by {interaction.user}: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to lock: {e}", ephemeral=True)
            return
        embed = discord.Embed(
            title="🔒 Channel Locked",
            description=f"{ch.mention} is now locked.\n**Reason:** {reason}",
            color=0xED4245,
        )
        await interaction.response.send_message(embed=embed)
        await send_log(self.bot, "STATUS", f"{interaction.user} **locked** {ch.mention}. Reason: {reason}")

    # ── /unlock ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="unlock", description="Unlock a previously locked channel")
    @discord.app_commands.describe(
        channel="Channel to unlock (defaults to current channel)",
        reason="Reason for unlocking",
    )
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ You need **Manage Channels** permission.", ephemeral=True)
            return
        ch = channel or interaction.channel
        ow = ch.overwrites_for(interaction.guild.default_role)
        if ow.send_messages is not False:
            await interaction.response.send_message(f"⚠️ {ch.mention} is not currently locked.", ephemeral=True)
            return
        ow.send_messages = None  # Reset → inherit from category / server default
        try:
            await ch.edit(overwrites={interaction.guild.default_role: ow}, reason=f"Unlocked by {interaction.user}: {reason}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unlock: {e}", ephemeral=True)
            return
        embed = discord.Embed(
            title="🔓 Channel Unlocked",
            description=f"{ch.mention} is now unlocked.\n**Reason:** {reason}",
            color=0x57F287,
        )
        await interaction.response.send_message(embed=embed)
        await send_log(self.bot, "STATUS", f"{interaction.user} **unlocked** {ch.mention}. Reason: {reason}")

    # ── /lockdown ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="lockdown", description="Lock ALL text channels — emergency use only")
    @discord.app_commands.describe(reason="Reason for the lockdown")
    async def lockdown(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)
            return
        await interaction.response.defer()
        locked = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(interaction.guild.default_role)
            if ow.send_messages is False:
                continue  # already locked, skip
            ow.send_messages = False
            try:
                await ch.edit(
                    overwrites={interaction.guild.default_role: ow},
                    reason=f"Lockdown by {interaction.user}: {reason}",
                )
                locked += 1
            except Exception:
                pass
        embed = discord.Embed(
            title="🚨 Server Lockdown Active",
            description=f"**{locked}** channel(s) locked.\n**Reason:** {reason}\n\nUse `/unlockdown` to lift.",
            color=0xED4245,
            timestamp=discord.utils.utcnow(),
        )
        await interaction.followup.send(embed=embed)
        await send_log(
            self.bot, "STATUS",
            f"{interaction.user} initiated **LOCKDOWN** — {locked} channel(s) locked. Reason: {reason}",
        )

    # ── /unlockdown ───────────────────────────────────────────────────────────
    @discord.app_commands.command(name="unlockdown", description="Lift the server lockdown and restore all channels")
    async def unlockdown(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)
            return
        await interaction.response.defer()
        unlocked = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(interaction.guild.default_role)
            if ow.send_messages is False:
                ow.send_messages = None
                try:
                    await ch.edit(
                        overwrites={interaction.guild.default_role: ow},
                        reason="Lockdown lifted",
                    )
                    unlocked += 1
                except Exception:
                    pass
        embed = discord.Embed(
            title="✅ Lockdown Lifted",
            description=f"**{unlocked}** channel(s) unlocked.",
            color=0x57F287,
            timestamp=discord.utils.utcnow(),
        )
        await interaction.followup.send(embed=embed)
        await send_log(
            self.bot, "STATUS",
            f"{interaction.user} lifted **LOCKDOWN** — {unlocked} channel(s) unlocked.",
        )

    # ── /purge ────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="purge", description="Delete recent messages from a specific user")
    @discord.app_commands.describe(
        user="The member whose messages to delete",
        amount="How many recent messages to scan (max 200, defaults to 50)",
    )
    async def purge(self, interaction: discord.Interaction, user: discord.Member, amount: int = 50):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need **Manage Messages** permission.", ephemeral=True)
            return
        if amount < 1 or amount > 200:
            await interaction.response.send_message("❌ Choose between 1 and 200.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount, check=lambda m: m.author.id == user.id)
        await interaction.followup.send(
            f"🧹 Deleted **{len(deleted)}** message(s) from {user.mention}.", ephemeral=True
        )
        await send_log(
            self.bot, "COMMAND",
            f"{interaction.user} purged **{len(deleted)}** message(s) from {user.mention} in {interaction.channel.mention}.",
        )

    # ── /softban ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="softban",
        description="Ban then instantly unban a member to delete their recent messages without a permanent ban",
    )
    @discord.app_commands.describe(
        member="The member to softban",
        delete_days="Days of messages to delete (1–7, defaults to 1)",
        reason="Reason for the softban",
    )
    async def softban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        delete_days: int = 1,
        reason: str = "No reason provided",
    ):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("❌ You need **Ban Members** permission.", ephemeral=True)
            return
        if delete_days < 1 or delete_days > 7:
            await interaction.response.send_message("❌ `delete_days` must be between 1 and 7.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await member.ban(delete_message_days=delete_days, reason=f"Softban by {interaction.user}: {reason}")
            await interaction.guild.unban(member, reason="Softban: immediate unban")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
            return
        await interaction.followup.send(
            f"🪃 **{member}** has been softbanned — messages from the last **{delete_days}** day(s) deleted. "
            f"They can still rejoin with an invite.",
            ephemeral=True,
        )
        await send_log(
            self.bot, "COMMAND",
            f"{interaction.user} **softbanned** {member.mention} (delete_days={delete_days}). Reason: {reason}",
        )

    # ── /role ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="role", description="Add or remove a role from a member")
    @discord.app_commands.describe(
        action="add or remove",
        member="Target member",
        role="Role to add or remove",
    )
    async def role(
        self,
        interaction: discord.Interaction,
        action: str,
        member: discord.Member,
        role: discord.Role,
    ):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You need **Manage Roles** permission.", ephemeral=True)
            return
        action = action.lower()
        if action not in ("add", "remove"):
            await interaction.response.send_message("❌ Action must be `add` or `remove`.", ephemeral=True)
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "❌ That role is above or equal to my highest role — I can't manage it.", ephemeral=True
            )
            return
        try:
            if action == "add":
                if role in member.roles:
                    await interaction.response.send_message(
                        f"⚠️ {member.mention} already has {role.mention}.", ephemeral=True
                    )
                    return
                await member.add_roles(role, reason=f"Role granted by {interaction.user}")
                await interaction.response.send_message(
                    f"✅ Gave {role.mention} to {member.mention}.", ephemeral=True
                )
            else:
                if role not in member.roles:
                    await interaction.response.send_message(
                        f"⚠️ {member.mention} does not have {role.mention}.", ephemeral=True
                    )
                    return
                await member.remove_roles(role, reason=f"Role removed by {interaction.user}")
                await interaction.response.send_message(
                    f"✅ Removed {role.mention} from {member.mention}.", ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)
            return
        await send_log(
            self.bot, "COMMAND",
            f"{interaction.user} **{action}ed** {role.mention} "
            f"{'to' if action == 'add' else 'from'} {member.mention}.",
        )
