import os
import time
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from commands import send_log, blocked
from features import load_guild_settings, save_guild_settings, get_guild_settings

load_dotenv()

DEV_ID = int(os.getenv("DEV_ID", "0"))

# AFK store: (guild_id, user_id) → {"reason": str, "set_at": float}
_afk_store: dict[tuple[int, int], dict] = {}

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


# ── CHANGELOG MODAL ───────────────────────────────────────────────────────────
class ChangelogModal(discord.ui.Modal, title="Post Changelog"):
    cl_title = discord.ui.TextInput(
        label="Update Title",
        placeholder="v2.4.0 — Performance Update",
        max_length=100,
    )
    cl_version = discord.ui.TextInput(
        label="Version Tag (optional)",
        placeholder="v2.4.0",
        required=False,
        max_length=30,
    )
    cl_body = discord.ui.TextInput(
        label="Changes",
        style=discord.TextStyle.paragraph,
        placeholder="\U0001f195 Added: ...\n\U0001f41b Fixed: ...\n\U0001f5d1\ufe0f Removed: ...",
        max_length=2000,
    )
    cl_image = discord.ui.TextInput(
        label="Banner Image URL (optional)",
        placeholder="https://i.imgur.com/example.png",
        required=False,
        max_length=500,
    )

    def __init__(self, webhook_url: str, auto_publish: bool, posted_by: str):
        super().__init__()
        self.webhook_url  = webhook_url
        self.auto_publish = auto_publish
        self.posted_by    = posted_by

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        version_tag = self.cl_version.value.strip()
        embed = discord.Embed(
            title=f"\U0001f4cb {self.cl_title.value.strip()}",
            description=self.cl_body.value,
            color=0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        if version_tag:
            embed.add_field(name="Version", value=f"`{version_tag}`", inline=True)
        embed.set_footer(text=f"Posted by {self.posted_by}")
        image_url = self.cl_image.value.strip()
        if image_url:
            embed.set_image(url=image_url)
        try:
            webhook = discord.Webhook.from_url(self.webhook_url, client=interaction.client)
            msg = await webhook.send(embed=embed, wait=True)
            if self.auto_publish:
                try:
                    await msg.publish()
                except (discord.Forbidden, discord.HTTPException):
                    pass  # Not an announcement channel or missing perms
        except Exception as e:
            await interaction.followup.send(f"\u274c Failed to send changelog: {e}", ephemeral=True)
            return
        await interaction.followup.send("\u2705 Changelog posted!", ephemeral=True)
        await send_log(
            interaction.client, "COMMAND",
            f"{interaction.user} posted a **changelog**: `{self.cl_title.value.strip()}`",
        )


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

    # ── /setchangelogwebhook ──────────────────────────────────────────────────
    @discord.app_commands.command(
        name="setchangelogwebhook",
        description="Set the Discord webhook URL used by /changelog",
    )
    @discord.app_commands.describe(url="Webhook URL from your changelog channel (Settings → Integrations → Webhooks)")
    async def setchangelogwebhook(self, interaction: discord.Interaction, url: str):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        url = url.strip()
        if not (
            url.startswith("https://discord.com/api/webhooks/")
            or url.startswith("https://discordapp.com/api/webhooks/")
            or url.startswith("https://ptb.discord.com/api/webhooks/")
            or url.startswith("https://canary.discord.com/api/webhooks/")
        ):
            await interaction.response.send_message(
                "❌ That doesn't look like a valid Discord webhook URL.\n"
                "Copy it from **Channel Settings → Integrations → Webhooks**.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings["changelog_webhook_url"] = url
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send(
            "✅ Changelog webhook saved. Use `/changelog` to post.", ephemeral=True
        )
        await send_log(self.bot, "STATUS", f"{interaction.user} configured the **changelog webhook**.")

    # ── /clearchangelogwebhook ────────────────────────────────────────────────
    @discord.app_commands.command(
        name="clearchangelogwebhook",
        description="Remove the configured changelog webhook",
    )
    async def clearchangelogwebhook(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ You need **Manage Server** permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        settings.pop("changelog_webhook_url", None)
        await save_guild_settings(interaction.guild_id, settings)
        await interaction.followup.send("✅ Changelog webhook removed.", ephemeral=True)
        await send_log(self.bot, "STATUS", f"{interaction.user} removed the **changelog webhook**.")

    # ── /changelog ────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="changelog",
        description="Post a custom changelog via webhook (staff only)",
    )
    @discord.app_commands.describe(
        auto_publish="Crosspost/publish the message — only works in Announcement channels (default: True)",
    )
    async def changelog(self, interaction: discord.Interaction, auto_publish: bool = True):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        settings = await load_guild_settings(interaction.guild_id)
        webhook_url = settings.get("changelog_webhook_url")
        if not webhook_url:
            await interaction.response.send_message(
                "❌ No changelog webhook configured.\n"
                "An admin needs to run `/setchangelogwebhook <url>` first.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ChangelogModal(
                webhook_url=webhook_url,
                auto_publish=auto_publish,
                posted_by=interaction.user.display_name,
            )
        )

    # ── /afk ──────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="afk", description="Set yourself as AFK — the bot will auto-reply when you're mentioned")
    @discord.app_commands.describe(reason="Why you're going AFK (optional)")
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"):
        if await blocked(interaction): return
        key = (interaction.guild_id, interaction.user.id)
        _afk_store[key] = {"reason": reason, "set_at": time.time()}
        embed = discord.Embed(
            description=f"💤 You're now AFK: **{reason}**",
            color=0xFEE75C,
        )
        embed.set_footer(text="Send any message to clear your AFK status")
        await interaction.response.send_message(embed=embed)

    # ── AFK on_message watcher ──────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        key = (message.guild.id, message.author.id)

        # Clear AFK if the author was AFK
        if key in _afk_store:
            # Ignore the bot's own AFK confirmation message (the /afk command response)
            # by checking that the content isn't empty and it's not an interaction followup
            afk_data = _afk_store.pop(key)
            elapsed = int(time.time() - afk_data["set_at"])
            if elapsed < 3:
                # Probably the interaction followup firing; restore and skip
                _afk_store[key] = afk_data
            else:
                m_ago = elapsed // 60
                label = f"{m_ago} minute{'s' if m_ago != 1 else ''} ago" if m_ago >= 1 else "just now"
                try:
                    await message.reply(
                        f"✅ Welcome back {message.author.mention}! AFK cleared (was away {label}).",
                        delete_after=8,
                        mention_author=False,
                    )
                except Exception:
                    pass

        # Notify about AFK mentions
        for mentioned in message.mentions:
            if mentioned.bot:
                continue
            mention_key = (message.guild.id, mentioned.id)
            if mention_key in _afk_store:
                afk_data = _afk_store[mention_key]
                elapsed = int(time.time() - afk_data["set_at"])
                m_ago = elapsed // 60
                label = f"{m_ago} minute{'s' if m_ago != 1 else ''} ago" if m_ago >= 1 else "just now"
                try:
                    await message.reply(
                        f"💤 **{mentioned.display_name}** is AFK: *{afk_data['reason']}* (set {label}).",
                        mention_author=False,
                    )
                except Exception:
                    pass

    # ── /setnick ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="setnick", description="Force a nickname on a member")
    @discord.app_commands.describe(member="Target member", nickname="New nickname (max 32 chars)")
    async def setnick(self, interaction: discord.Interaction, member: discord.Member, nickname: str):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_nicknames:
            await interaction.response.send_message("\u274c You need **Manage Nicknames** permission.", ephemeral=True)
            return
        if len(nickname) > 32:
            await interaction.response.send_message("\u274c Nickname cannot exceed 32 characters.", ephemeral=True)
            return
        try:
            await member.edit(nick=nickname, reason=f"Nickname set by {interaction.user}")
            await interaction.response.send_message(f"✅ Set {member.mention}'s nickname to **{nickname}**.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("\u274c I can't change that member's nickname (they may be above me in role hierarchy).", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"\u274c Failed: {e}", ephemeral=True)
            return
        await send_log(self.bot, "COMMAND", f"{interaction.user} set {member.mention}'s nickname to `{nickname}`.")

    # ── /resetnick ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="resetnick", description="Reset a member's nickname to their username")
    @discord.app_commands.describe(member="Target member")
    async def resetnick(self, interaction: discord.Interaction, member: discord.Member):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_nicknames:
            await interaction.response.send_message("\u274c You need **Manage Nicknames** permission.", ephemeral=True)
            return
        try:
            await member.edit(nick=None, reason=f"Nickname reset by {interaction.user}")
            await interaction.response.send_message(f"✅ Reset {member.mention}'s nickname.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("\u274c I can't change that member's nickname.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"\u274c Failed: {e}", ephemeral=True)
            return
        await send_log(self.bot, "COMMAND", f"{interaction.user} reset {member.mention}'s nickname.")

    # ── /statschannel ────────────────────────────────────────────────────────
    _STAT_LABELS = {
        "members": ("👥", "Members"),
        "bots":    ("🤖", "Bots"),
        "boosts":  ("✨", "Boosts"),
    }

    @discord.app_commands.command(name="statschannel", description="Configure voice channels that display live server stats")
    @discord.app_commands.describe(
        action="set, clear, or list",
        stat_type="members, bots, or boosts",
        channel="Voice channel to use as the stats display",
    )
    async def statschannel(
        self,
        interaction: discord.Interaction,
        action: str,
        stat_type: str = None,
        channel: discord.VoiceChannel = None,
    ):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("\u274c You need **Manage Server** permission.", ephemeral=True)
            return
        action = action.lower()
        await interaction.response.defer(ephemeral=True)
        settings = await load_guild_settings(interaction.guild_id)
        stats_cfg = settings.setdefault("stats_channels", {})

        if action == "set":
            if not stat_type or not channel:
                await interaction.followup.send("\u274c Provide both a `stat_type` and a `channel`.", ephemeral=True)
                return
            stat_type = stat_type.lower()
            if stat_type not in self._STAT_LABELS:
                await interaction.followup.send(f"\u274c Valid types: `{'`, `'.join(self._STAT_LABELS)}`", ephemeral=True)
                return
            stats_cfg[stat_type] = channel.id
            settings["stats_channels"] = stats_cfg
            await save_guild_settings(interaction.guild_id, settings)
            emoji, label = self._STAT_LABELS[stat_type]
            await interaction.followup.send(
                f"✅ **{channel.name}** will now display `{emoji} {label}: ...` and update every 10 minutes.",
                ephemeral=True,
            )

        elif action == "clear":
            if not stat_type:
                await interaction.followup.send("\u274c Provide a `stat_type` to clear.", ephemeral=True)
                return
            stat_type = stat_type.lower()
            if stats_cfg.pop(stat_type, None) is not None:
                settings["stats_channels"] = stats_cfg
                await save_guild_settings(interaction.guild_id, settings)
                await interaction.followup.send(f"✅ Cleared `{stat_type}` stats channel.", ephemeral=True)
            else:
                await interaction.followup.send(f"⚠️ No `{stat_type}` stats channel was configured.", ephemeral=True)

        elif action == "list":
            if not stats_cfg:
                await interaction.followup.send("⚠️ No stats channels configured. Use `/statschannel set`.", ephemeral=True)
                return
            lines = []
            for t, cid in stats_cfg.items():
                emoji, label = self._STAT_LABELS.get(t, ("📊", t.title()))
                ch = interaction.guild.get_channel(int(cid))
                ch_str = ch.mention if ch else f"*deleted channel* ({cid})"
                lines.append(f"`{t}` → {ch_str}")
            await interaction.followup.send("ℹ️ Stats channels:\n" + "\n".join(lines), ephemeral=True)

        else:
            await interaction.followup.send("\u274c Use `set`, `clear`, or `list`.", ephemeral=True)

    # ── Stats channel background updater ──────────────────────────────────────
    def cog_load(self):
        self._stats_update.start()

    def cog_unload(self):
        self._stats_update.cancel()

    @tasks.loop(minutes=10)
    async def _stats_update(self):
        for guild in self.bot.guilds:
            settings = get_guild_settings(guild.id)
            stats_cfg = settings.get("stats_channels", {})
            if not stats_cfg:
                continue
            humans = sum(1 for m in guild.members if not m.bot)
            bots   = sum(1 for m in guild.members if m.bot)
            boosts = guild.premium_subscription_count or 0
            values = {"members": humans, "bots": bots, "boosts": boosts}
            for stat_type, channel_id in list(stats_cfg.items()):
                emoji, label = self._STAT_LABELS.get(stat_type, ("📊", stat_type.title()))
                new_name = f"{emoji} {label}: {values.get(stat_type, 0):,}"
                ch = guild.get_channel(int(channel_id))
                if not ch or not isinstance(ch, discord.VoiceChannel):
                    continue
                if ch.name == new_name:
                    continue  # no change, skip to avoid unnecessary API call
                try:
                    await ch.edit(name=new_name, reason="Stats channel update")
                except Exception:
                    pass

    @_stats_update.before_loop
    async def _before_stats_update(self):
        await self.bot.wait_until_ready()
