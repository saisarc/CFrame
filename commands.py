import os
import time
import asyncio
import psutil
import aiohttp
import discord
from groq import AsyncGroq
from datetime import datetime, timezone
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
TARGET_PLACE_ID = "9399205659"
DEV_ID          = int(os.getenv("DEV_ID", "0"))
LOG_CHANNEL_ID  = int(os.getenv("LOG_CHANNEL_ID", "0"))

ai_client       = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
chat_histories  = {}
beta_signups    = []
beta_open       = False
disabled_cmds   = set()
countdowns      = {}   # { message_id: { title, end_time, channel_id } }
patch_previews  = {}   # { message_id: { full_title, full_body, full_image } }

# ── LOGGING ───────────────────────────────────────────────────────────────────
async def send_log(bot: discord.Client, category: str, description: str, color: int = 0x5865F2, error: bool = False):
    if not LOG_CHANNEL_ID:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    icons = {
        "STATUS":  "🟢",
        "COMMAND": "⚙️",
        "ERROR":   "🔴",
        "RESTART": "🔄",
        "CRASH":   "💥",
        "REQUEST": "📡",
    }
    icon  = icons.get(category.upper(), "📋")
    embed = discord.Embed(
        title       = f"{icon} {category.upper()}",
        description = description,
        color       = 0xFF6B6B if error else color,
        timestamp   = datetime.now(timezone.utc),
    )
    embed.set_footer(text="CFrame Bot Logs")
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

# ── HELPERS ───────────────────────────────────────────────────────────────────
def is_dev(interaction: discord.Interaction) -> bool:
    return interaction.user.id == DEV_ID

def is_disabled(cmd: str) -> bool:
    return cmd in disabled_cmds

async def blocked(interaction: discord.Interaction) -> bool:
    if is_disabled(interaction.command.name):
        await interaction.response.send_message("🚫 This command is currently disabled.", ephemeral=True)
        return True
    return False

def fmt_countdown(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem  = divmod(seconds, 86400)
    h, rem  = divmod(rem, 3600)
    m, s    = divmod(rem, 60)
    parts   = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

# ── ROBLOX HELPERS ────────────────────────────────────────────────────────────
async def place_to_universe(session: aiohttp.ClientSession, place_id: str):
    url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        return str((await r.json()).get("universeId"))

async def get_game_data(session: aiohttp.ClientSession, universe_id: str):
    url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return None
        data  = await r.json()
        games = data.get("data", [])
        return games[0] if games else None

# ── MODALS ────────────────────────────────────────────────────────────────────
class DevlogModal(discord.ui.Modal, title="Post Dev Log"):
    log_title = discord.ui.TextInput(label="Title", placeholder="v1.2 — New Map Update", max_length=100)
    log_body  = discord.ui.TextInput(label="What's new?", style=discord.TextStyle.paragraph,
                                     placeholder="- Added new map\n- Fixed bug\n- Better performance", max_length=2000)
    log_image = discord.ui.TextInput(label="Image URL (optional)", placeholder="https://i.imgur.com/example.png",
                                     required=False, max_length=500)
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=f"📋 Dev Log — {self.log_title.value}", description=self.log_body.value, color=0x5865F2)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if self.log_image.value:
            embed.set_image(url=self.log_image.value)
        embed.set_footer(text=f"CFrame Dev Log · {time.strftime('%B %d, %Y')}")
        await interaction.response.send_message(embed=embed)
        await send_log(interaction.client, "COMMAND", f"{interaction.user} posted a **devlog**: `{self.log_title.value}`")

class UpdateModal(discord.ui.Modal, title="Post Update"):
    update_version = discord.ui.TextInput(label="Version", placeholder="v1.2.0", max_length=30)
    update_body    = discord.ui.TextInput(label="Patch Notes", style=discord.TextStyle.paragraph,
                                          placeholder="🆕 Added: ...\n🐛 Fixed: ...\n🗑️ Removed: ...", max_length=2000)
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=f"🚀 Update {self.update_version.value}", description=self.update_body.value, color=0xFEE75C)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"CFrame Bot · {time.strftime('%B %d, %Y')}")
        await interaction.response.send_message(embed=embed)
        await send_log(interaction.client, "COMMAND", f"{interaction.user} posted an **update**: `{self.update_version.value}`")

class HypeModal(discord.ui.Modal, title="Post Hype Announcement"):
    hype_title = discord.ui.TextInput(label="Title", placeholder="Something BIG is coming...", max_length=100)
    hype_body  = discord.ui.TextInput(label="Teaser message", style=discord.TextStyle.paragraph,
                                      placeholder="Drop a hype message here. Be mysterious 👀", max_length=1000)
    hype_date  = discord.ui.TextInput(label="Date / Time (optional)", placeholder="Friday at 5PM EST",
                                      required=False, max_length=100)
    hype_image = discord.ui.TextInput(label="Banner image URL (optional)", placeholder="https://i.imgur.com/example.png",
                                      required=False, max_length=500)
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=f"🔥 {self.hype_title.value}", description=self.hype_body.value, color=0xFF4500)
        if self.hype_date.value:
            embed.add_field(name="📅 When", value=self.hype_date.value, inline=False)
        if self.hype_image.value:
            embed.set_image(url=self.hype_image.value)
        embed.set_footer(text="CFrame Bot · Stay tuned 👀")
        await interaction.response.send_message("@here", embed=embed)
        await send_log(interaction.client, "COMMAND", f"{interaction.user} posted a **hype** announcement: `{self.hype_title.value}`")

class CountdownModal(discord.ui.Modal, title="Create Countdown"):
    cd_title   = discord.ui.TextInput(label="Event name", placeholder="New Map Launch", max_length=100)
    cd_hours   = discord.ui.TextInput(label="Hours from now", placeholder="24", max_length=6)
    cd_message = discord.ui.TextInput(label="Description (optional)", style=discord.TextStyle.paragraph,
                                      placeholder="Something big is dropping!", required=False, max_length=500)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            hours    = float(self.cd_hours.value)
            end_time = time.time() + hours * 3600
        except ValueError:
            await interaction.response.send_message("❌ Hours must be a number.", ephemeral=True)
            return

        embed = discord.Embed(
            title       = f"⏳ {self.cd_title.value}",
            description = self.cd_message.value or "",
            color       = 0xFEE75C,
        )
        embed.add_field(name="⏱️ Time Remaining", value=fmt_countdown(end_time - time.time()), inline=False)
        embed.set_footer(text="CFrame Bot · Countdown")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        countdowns[msg.id] = {
            "title":      self.cd_title.value,
            "description": self.cd_message.value or "",
            "end_time":   end_time,
            "channel_id": interaction.channel_id,
            "message_id": msg.id,
        }
        await send_log(interaction.client, "COMMAND", f"{interaction.user} started a **countdown**: `{self.cd_title.value}` ({hours}h)")

class PatchPreviewModal(discord.ui.Modal, title="Post Patch Preview"):
    preview_title  = discord.ui.TextInput(label="Teaser title", placeholder="Something is coming...", max_length=100)
    preview_teaser = discord.ui.TextInput(label="Teaser text (shown now)", style=discord.TextStyle.paragraph,
                                          placeholder="A major change is on the way. Prepare yourself.", max_length=500)
    preview_full   = discord.ui.TextInput(label="Full reveal text (hidden until /patchpreview reveal)",
                                          style=discord.TextStyle.paragraph,
                                          placeholder="Full patch notes here — not shown until you reveal", max_length=2000)
    preview_image  = discord.ui.TextInput(label="Image URL (shown on reveal)", placeholder="https://i.imgur.com/example.png",
                                          required=False, max_length=500)
    async def on_submit(self, interaction: discord.Interaction):
        # Build the censored teaser
        censored = "\n".join(f"||{line}||" if i > 0 else line for i, line in enumerate(self.preview_teaser.value.split("\n")))
        embed = discord.Embed(title=f"👁️ {self.preview_title.value}", description=censored, color=0x2C2F33)
        embed.add_field(name="​", value="*Full details will be revealed soon...*", inline=False)
        embed.set_footer(text="CFrame Bot · Patch Preview — use /patchpreview reveal <message_id> to reveal")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        patch_previews[str(msg.id)] = {
            "title":      self.preview_title.value,
            "full_body":  self.preview_full.value,
            "image":      self.preview_image.value or None,
            "channel_id": interaction.channel_id,
        }
        await send_log(interaction.client, "COMMAND", f"{interaction.user} posted a **patch preview**: `{self.preview_title.value}` (ID: {msg.id})")


# ── COG ───────────────────────────────────────────────────────────────────────
class GameCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, start_time: float):
        self.bot        = bot
        self.start_time = start_time
        self.countdown_task.start()

    def cog_unload(self):
        self.countdown_task.cancel()

    # ── BACKGROUND: countdown updater ─────────────────────────────────────────
    @tasks.loop(minutes=1)
    async def countdown_task(self):
        expired = []
        for msg_id, data in countdowns.items():
            remaining = data["end_time"] - time.time()
            channel   = self.bot.get_channel(data["channel_id"])
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(msg_id)
            except Exception:
                expired.append(msg_id)
                continue

            if remaining <= 0:
                embed = discord.Embed(
                    title       = f"🎉 {data['title']} — It's time!",
                    description = data.get("description", ""),
                    color       = 0x57F287,
                )
                embed.set_footer(text="CFrame Bot · Countdown finished!")
                await msg.edit(embed=embed)
                expired.append(msg_id)
                await send_log(self.bot, "STATUS", f"Countdown **{data['title']}** has finished.")
            else:
                embed = discord.Embed(
                    title       = f"⏳ {data['title']}",
                    description = data.get("description", ""),
                    color       = 0xFEE75C,
                )
                embed.add_field(name="⏱️ Time Remaining", value=fmt_countdown(remaining), inline=False)
                embed.set_footer(text="CFrame Bot · Updates every minute")
                await msg.edit(embed=embed)

        for mid in expired:
            countdowns.pop(mid, None)

    @countdown_task.before_loop
    async def before_countdown(self):
        await self.bot.wait_until_ready()

    # ── ERROR HANDLER ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        cmd = interaction.command.name if interaction.command else "unknown"
        await send_log(
            self.bot, "ERROR",
            f"**Command:** `/{cmd}`\n**User:** {interaction.user}\n**Error:** `{error}`",
            error=True,
        )
        try:
            await interaction.followup.send(f"❌ An error occurred: `{error}`", ephemeral=True)
        except Exception:
            pass

    # ── COMMAND LOGGER ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.application_command:
            return
        cmd = interaction.command.name if interaction.command else "unknown"
        if cmd in ("devlog", "updates", "hype", "countdown", "patchpreview"):
            return  # these log themselves in the modal
        await send_log(
            self.bot, "COMMAND",
            f"**/{cmd}** used by **{interaction.user}** in <#{interaction.channel_id}>",
            color=0x5865F2,
        )

    # ── /status ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="status", description="Check bot and system diagnostics")
    async def status(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        await interaction.response.defer()

        uptime_secs = int(time.time() - self.start_time)
        h, rem = divmod(uptime_secs, 3600)
        m, s = divmod(rem, 60)

        cpu = psutil.cpu_percent(interval=0.5)
        try:
            ram_percent = psutil.virtual_memory().percent
        except Exception:
            ram_percent = "N/A"

        total_users = sum(g.member_count or 0 for g in self.bot.guilds)

        # More professional RAM: percent + bot RSS (host process memory)
        try:
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            ram_field = f"{ram_percent}% ({mem_mb:.1f} MB RSS)" if ram_percent != "N/A" else f"{mem_mb:.1f} MB RSS"
        except Exception:
            ram_field = f"{ram_percent}%" if ram_percent != "N/A" else "N/A"

        embed = discord.Embed(
            title="🟢 CFrame Status",
            description="Diagnostics for uptime, performance, and bot readiness.",
            color=0x57F287,
        )

        embed.add_field(name="Status", value="Online ✅", inline=True)
        embed.add_field(name="Uptime", value=f"{h}h {m}m {s}s", inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)

        embed.add_field(name="CPU", value=f"{cpu}%", inline=True)
        embed.add_field(name="RAM", value=ram_field, inline=True)

        embed.add_field(name="AI", value="Ready", inline=True)
        embed.add_field(name="Servers", value=f"{len(self.bot.guilds)}", inline=True)
        embed.add_field(name="Users", value=f"{total_users:,}", inline=True)
        
        # Guild-specific music info
        guild = interaction.guild
        if guild:
            voice_client = guild.voice_client
            guild_status_value = f"**Server:** {guild.name}\n"
            
            if voice_client:
                if hasattr(voice_client, 'channel') and voice_client.channel:
                    guild_status_value += f"**Voice:** In {voice_client.channel.name} ✅\n"
                    member_count = len(voice_client.channel.members)
                    guild_status_value += f"**Members:** {member_count} in voice\n"
                else:
                    guild_status_value += "**Voice:** Connected\n"
                
                # Get queue info
                if hasattr(voice_client, 'current'):
                    current = voice_client.current
                    if current:
                        title = getattr(current, 'title', 'Unknown')
                        guild_status_value += f"**Now Playing:** {title}\n"
                
                if hasattr(voice_client, 'queue'):
                    queue_len = len(voice_client.queue) if voice_client.queue else 0
                    guild_status_value += f"**Queued:** {queue_len} tracks"
            else:
                guild_status_value += "**Voice:** Not connected"
            
            embed.add_field(name="📊 Guild Music Status", value=guild_status_value, inline=False)

        embed.add_field(
            name="Notes",
            value=(
                "• Uptime + latency are live runtime values\n"
                "• CPU/RAM reflect the host machine\n"
                "• Guild status shows this server's voice and queue info\n"
                "• Use `/help` for command navigation"
            ),
            inline=False,
        )

        embed.set_footer(text="CFrame Bot · Status")
        await interaction.followup.send(embed=embed)


    # ── /players ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="players", description="Get live player count for your Roblox game")
    async def players(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        await interaction.response.defer()

        async with aiohttp.ClientSession() as session:
            universe_id = await place_to_universe(session, TARGET_PLACE_ID)
            if not universe_id:
                await interaction.followup.send("❌ Could not resolve Place ID to Universe ID.")
                return
            game = await get_game_data(session, universe_id)
            if not game:
                await interaction.followup.send("❌ Could not fetch game data from Roblox.")
                return

        playing     = game.get("playing", 0)
        visits      = game.get("visits", 0)
        max_players = game.get("maxPlayers", "?")
        name        = game.get("name", "Unknown")
        game_url    = f"https://www.roblox.com/games/{TARGET_PLACE_ID}"

        embed = discord.Embed(
            title=f"🎮 {name}",
            description=f"Live Roblox game data for the current experience.",
            url=game_url,
            color=0x57F287 if playing > 0 else 0x99AAB5,
        )
        embed.add_field(name="👥 Playing Now", value=f"{playing:,}", inline=True)
        embed.add_field(name="🏆 Max Players", value=str(max_players), inline=True)
        embed.add_field(name="👁️ Total Visits", value=f"{visits:,}", inline=True)
        embed.add_field(name="🔗 Roblox Link", value=f"[Open Game]({game_url})", inline=False)
        embed.set_footer(text="CFrame Bot · Live Data")
        await interaction.followup.send(embed=embed)

    # ── /chat ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="chat", description="Chat with the CFrame AI")
    @discord.app_commands.describe(message="Your message")
    async def chat(self, interaction: discord.Interaction, message: str):
        if await blocked(interaction): return
        await interaction.response.defer()

        uid     = interaction.user.id
        history = chat_histories.setdefault(uid, [])
        history.append({"role": "user", "content": message})
        if len(history) > 20:
            history[:] = history[-20:]

        try:
            response = await ai_client.chat.completions.create(
                model    = "llama-3.1-8b-instant",
                messages = [
                    {"role": "system", "content": (
                        "You are CFrame AI, a helpful assistant built into the CFrame Discord bot. "
                        "Never mention any AI company or what model you are. "
                        "Keep replies concise and Discord-friendly. "
                        "Use markdown sparingly — bold and code blocks are fine."
                    )},
                    *history
                ],
                max_tokens = 1024,
            )
            reply = response.choices[0].message.content
            history.append({"role": "assistant", "content": reply})
        except Exception as e:
            await interaction.followup.send(f"❌ AI error: `{e}`")
            return

        turns = len(history) // 2
        embed = discord.Embed(description=reply[:4096], color=0xA855F7)
        embed.set_author(name=f"{interaction.user.display_name} asked:", icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"💬 {turns} turn(s) · /clearchat to reset | CFrame AI")
        await interaction.followup.send(embed=embed)

    # ── /clearchat ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="clearchat", description="Clear your AI conversation history")
    async def clearchat(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        chat_histories.pop(interaction.user.id, None)
        await interaction.response.send_message("🧹 Conversation history cleared!", ephemeral=True)

    # ── /devlog ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="devlog", description="Post a development log (staff only)")
    async def devlog(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(DevlogModal())

    # ── /updates ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="updates", description="Post a game update / patch notes (staff only)")
    async def updates(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(UpdateModal())

    # ── /testing ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="testing", description="Manage beta testing signups")
    @discord.app_commands.describe(action="open, close, join, or list", link="Private server link (used with 'close')")
    async def testing(self, interaction: discord.Interaction, action: str, link: str = None):
        if await blocked(interaction): return
        global beta_open, beta_signups
        action = action.lower()

        if action == "open":
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            beta_open    = True
            beta_signups = []
            embed = discord.Embed(title="🧪 Beta Testing Open!", color=0x57F287,
                                  description="Signups are now open! Use `/testing join` to sign up.")
            embed.set_footer(text="CFrame Bot · Staff will DM you the link when testing begins")
            await interaction.response.send_message(embed=embed)
            await send_log(self.bot, "STATUS", f"{interaction.user} **opened** beta testing signups.")

        elif action == "join":
            if not beta_open:
                await interaction.response.send_message("❌ Beta signups are not open right now.", ephemeral=True)
                return
            if interaction.user.id in beta_signups:
                await interaction.response.send_message("✅ You're already signed up!", ephemeral=True)
                return
            beta_signups.append(interaction.user.id)
            await interaction.response.send_message(
                f"✅ You're signed up! We'll DM you when it starts. ({len(beta_signups)} total)", ephemeral=True)

        elif action == "close":
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            if not beta_open:
                await interaction.response.send_message("❌ Beta signups aren't open.", ephemeral=True)
                return
            beta_open    = False
            sent, failed = 0, 0
            if link and beta_signups:
                dm_embed = discord.Embed(title="🎮 You're in the Beta!",
                                         description=f"Here's your private server link:\n\n**{link}**", color=0x5865F2)
                dm_embed.set_footer(text="CFrame Bot · Don't share this link!")
                for uid in beta_signups:
                    try:
                        user = await interaction.client.fetch_user(uid)
                        await user.send(embed=dm_embed)
                        sent += 1
                    except Exception:
                        failed += 1
            embed = discord.Embed(title="🧪 Beta Signups Closed",
                                  description=f"**{len(beta_signups)}** signed up.\n✅ DMed: {sent} | ❌ Failed: {failed}",
                                  color=0xFF6B6B)
            beta_signups = []
            await interaction.response.send_message(embed=embed)
            await send_log(self.bot, "STATUS", f"{interaction.user} **closed** beta testing. {sent} testers DMed.")

        elif action == "list":
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
                return
            embed = discord.Embed(title="🧪 Beta Testing Status", color=0x5865F2)
            embed.add_field(name="Status",  value="🟢 Open" if beta_open else "🔴 Closed", inline=True)
            embed.add_field(name="Signups", value=str(len(beta_signups)),                   inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        else:
            await interaction.response.send_message("❌ Invalid action. Use `open`, `close`, `join`, or `list`.", ephemeral=True)

    # ── /hype ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="hype", description="Post a hype announcement (staff only)")
    async def hype(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(HypeModal())

    # ── /countdown ────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="countdown", description="Start a live countdown to an event (staff only)")
    async def countdown(self, interaction: discord.Interaction):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_modal(CountdownModal())

    # ── /patchpreview ─────────────────────────────────────────────────────────
    @discord.app_commands.command(name="patchpreview", description="Tease an upcoming patch (staff only)")
    @discord.app_commands.describe(action="post or reveal", message_id="Message ID to reveal (used with 'reveal')")
    async def patchpreview(self, interaction: discord.Interaction, action: str, message_id: str = None):
        if await blocked(interaction): return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return

        if action == "post":
            await interaction.response.send_modal(PatchPreviewModal())

        elif action == "reveal":
            if not message_id or message_id not in patch_previews:
                await interaction.response.send_message(
                    "❌ No preview found with that message ID. Make sure you copy it correctly.", ephemeral=True)
                return
            data    = patch_previews.pop(message_id)
            channel = interaction.channel
            embed   = discord.Embed(
                title       = f"🚨 REVEALED — {data['title']}",
                description = data["full_body"],
                color       = 0xFF4500,
            )
            if data.get("image"):
                embed.set_image(url=data["image"])
            embed.set_footer(text=f"CFrame Bot · Patch Reveal · {time.strftime('%B %d, %Y')}")
            await interaction.response.send_message(embed=embed)
            await send_log(self.bot, "COMMAND", f"{interaction.user} **revealed** patch preview `{data['title']}`")

        else:
            await interaction.response.send_message("❌ Invalid action. Use `post` or `reveal`.", ephemeral=True)

    # ── /dev ──────────────────────────────────────────────────────────────────
    @discord.app_commands.command(name="dev", description="Developer controls (owner only)")
    @discord.app_commands.describe(action="enable, disable, or list", command="Command to enable/disable")
    async def dev(self, interaction: discord.Interaction, action: str, command: str = None):
        if not is_dev(interaction):
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return

        action   = action.lower()
        all_cmds = ["status", "players", "chat", "clearchat", "devlog",
                    "updates", "testing", "hype", "countdown", "patchpreview", "help", "serverinfo", "userinfo",
                    "roadmap", "modhelp", "modsettings", "setlogchannel", "clearlogchannel", "setwelcomechannel",
                    "setwelcomemessage", "disablewelcome", "welcometest", "warn", "warnings",
                    "clearwarnings", "mute", "unmute", "kick", "ban", "unban", "clear", "slowmode",
                    "gstart", "gend", "greroll", "level", "leaderboard", "toggleleveling",
                    "toggleantispam", "setantispamthreshold", "autorole", "reactionrole"]

        if action == "disable":
            if not command or command not in all_cmds:
                await interaction.response.send_message(f"❌ Unknown command. Options: {', '.join(all_cmds)}", ephemeral=True)
                return
            disabled_cmds.add(command)
            await interaction.response.send_message(f"🔴 `/{command}` **disabled**.", ephemeral=True)
            await send_log(self.bot, "STATUS", f"Dev disabled `/{command}`.")

        elif action == "enable":
            if not command:
                await interaction.response.send_message("❌ Provide a command name.", ephemeral=True)
                return
            disabled_cmds.discard(command)
            await interaction.response.send_message(f"🟢 `/{command}` **enabled**.", ephemeral=True)
            await send_log(self.bot, "STATUS", f"Dev enabled `/{command}`.")

        elif action == "list":
            lines = [f"`/{c}` — {'🔴 Disabled' if c in disabled_cmds else '🟢 Enabled'}" for c in all_cmds]
            embed = discord.Embed(
                title="🛠️ Command Status",
                description="A quick overview of the slash commands currently available.",
                color=0x5865F2,
            )
            embed.add_field(name="Commands", value="\n".join(lines), inline=False)
            embed.set_footer(text="Only you can see this · CFrame Dev Panel")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        else:
            await interaction.response.send_message("❌ Use `enable`, `disable`, or `list`.", ephemeral=True)