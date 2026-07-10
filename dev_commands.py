import os
import io
import time
import asyncio
import contextlib
import traceback
import discord
from discord.ext import commands
from dotenv import load_dotenv
from commands import send_log, disabled_cmds

load_dotenv()
DEV_ID = int(os.getenv("DEV_ID", "0"))

ALL_CMDS = [
    "status", "players", "chat", "clearchat", "devlog",
    "updates", "testing", "hype", "countdown", "patchpreview", "changelog",
    "help", "serverinfo", "userinfo", "roadmap", "modhelp", "modsettings",
    "setlogchannel", "clearlogchannel", "setwelcomechannel",
    "setwelcomemessage", "disablewelcome", "welcometest", "warn",
    "warnings", "clearwarnings", "mute", "unmute", "kick", "ban",
    "unban", "clear", "slowmode", "gstart", "gend", "greroll",
    "level", "leaderboard", "toggleleveling", "toggleantispam",
    "setantispamthreshold", "autorole", "reactionrole",
    "setservericon", "setserverbanner", "setbotavatar",
    "setvanityrole", "clearvanityrole",
    "lock", "unlock", "lockdown", "unlockdown",
    "purge", "softban", "role",
    "setchangelogwebhook", "clearchangelogwebhook",
    "afk", "setnick", "resetnick", "statschannel",
    "customcmd", "poll",
    "dev"
]

def dev_only():
    async def predicate(ctx: commands.Context):
        if ctx.author.id != DEV_ID:
            await ctx.message.add_reaction("🚫")
            return False
        return True
    return commands.check(predicate)

class DevCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, start_time: float):
        self.bot        = bot
        self.start_time = start_time

    # ── !devhelp ──────────────────────────────────────────────────────────────
    @commands.command(name="devhelp")
    @dev_only()
    async def devhelp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🛠️ Dev Prefix Commands",
            description="Owner-only prefix commands for bot maintenance and testing.",
            color=0x5865F2,
        )
        cmds = [
            ("!devhelp",              "Show this message"),
            ("!sync",                 "Re-sync all slash commands"),
            ("!reload",               "Reload the commands cog"),
            ("!shutdown",             "Shut the bot down"),
            ("!disable <cmd>",        "Disable a slash command"),
            ("!enable <cmd>",         "Enable a slash command"),
            ("!cmdlist",              "List all slash commands and status"),
            ("!announce <#ch> <msg>", "Post an announcement to any channel"),
            ("!dm <user_id> <msg>",   "DM any user as the bot"),
            ("!purge <amount>",       "Delete messages in current channel"),
            ("!maintenance <on/off>", "Toggle maintenance mode (overrides all statuses)"),
            ("!log <message>",        "Send a test message to log channel"),
            ("!eval <code>",          "Run Python code"),
            ("!uptime",               "Quick uptime check"),
            ("!botstats",             "Full bot stats"),
        ]
        for name, desc in cmds:
            embed.add_field(name=f"`{name}`", value=desc, inline=False)
        embed.set_footer(text="Only you can run these · CFrame Dev")
        await ctx.send(embed=embed)

    # ── !sync ─────────────────────────────────────────────────────────────────
    @commands.command(name="sync")
    @dev_only()
    async def sync(self, ctx: commands.Context):
        msg = await ctx.send("🔄 Syncing commands...")
        synced = await self.bot.tree.sync()
        await msg.edit(content=f"✅ Synced **{len(synced)}** commands.")
        await send_log(self.bot, "STATUS", f"Dev manually synced **{len(synced)}** slash commands.")

    # ── !reload ───────────────────────────────────────────────────────────────
    @commands.command(name="reload")
    @dev_only()
    async def reload(self, ctx: commands.Context):
        msg = await ctx.send("🔄 Reloading cog...")
        try:
            await self.bot.reload_extension("commands")
            await msg.edit(content="✅ Cog reloaded successfully.")
            await send_log(self.bot, "RESTART", "Dev reloaded the **commands cog**.")
        except Exception as e:
            await msg.edit(content=f"❌ Reload failed: `{e}`")
            await send_log(self.bot, "ERROR", f"Cog reload failed: `{e}`", error=True)

    # ── !shutdown ─────────────────────────────────────────────────────────────
    @commands.command(name="shutdown")
    @dev_only()
    async def shutdown(self, ctx: commands.Context):
        await ctx.send("👋 Shutting down...")
        await send_log(self.bot, "RESTART", f"⛔ Bot **shut down** by {ctx.author}.")
        await self.bot.close()

    # ── !disable ──────────────────────────────────────────────────────────────
    @commands.command(name="disable")
    @dev_only()
    async def disable(self, ctx: commands.Context, cmd: str):
        if cmd not in ALL_CMDS:
            await ctx.send(f"❌ Unknown command. Options: `{'`, `'.join(ALL_CMDS)}`")
            return
        disabled_cmds.add(cmd)
        await ctx.send(f"🔴 `/{cmd}` is now **disabled**.")
        await send_log(self.bot, "STATUS", f"Dev disabled `/{cmd}`.")

    # ── !enable ───────────────────────────────────────────────────────────────
    @commands.command(name="enable")
    @dev_only()
    async def enable(self, ctx: commands.Context, cmd: str):
        disabled_cmds.discard(cmd)
        await ctx.send(f"🟢 `/{cmd}` is now **enabled**.")
        await send_log(self.bot, "STATUS", f"Dev enabled `/{cmd}`.")

    # ── !cmdlist ──────────────────────────────────────────────────────────────
    @commands.command(name="cmdlist")
    @dev_only()
    async def cmdlist(self, ctx: commands.Context):
        lines = [f"`/{c}` — {'🔴 Disabled' if c in disabled_cmds else '🟢 Enabled'}" for c in ALL_CMDS]
        embed = discord.Embed(
            title="📋 Slash Command Status",
            description="Current status of the bot's slash commands.",
            color=0x5865F2,
        )
        embed.add_field(name="Commands", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    # ── !announce ─────────────────────────────────────────────────────────────
    @commands.command(name="announce")
    @dev_only()
    async def announce(self, ctx: commands.Context, channel: discord.TextChannel, *, message: str):
        embed = discord.Embed(description=message, color=0xFEE75C)
        embed.set_author(name="📢 Announcement")
        embed.set_footer(text="CFrame Bot")
        await channel.send(embed=embed)
        await ctx.message.add_reaction("✅")
        await send_log(self.bot, "COMMAND", f"Dev posted an **announcement** to {channel.mention}.")

    # ── !dm ───────────────────────────────────────────────────────────────────
    @commands.command(name="dm")
    @dev_only()
    async def dm(self, ctx: commands.Context, user_id: int, *, message: str):
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message)
            await ctx.send(f"✅ DM sent to **{user}**.")
            await send_log(self.bot, "COMMAND", f"Dev DMed `{user}` (`{user_id}`).")
        except Exception as e:
            await ctx.send(f"❌ Failed to DM: `{e}`")

    # ── !purge ────────────────────────────────────────────────────────────────
    @commands.command(name="purge")
    @dev_only()
    async def purge(self, ctx: commands.Context, amount: int):
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=amount)
        msg = await ctx.send(f"🧹 Deleted **{len(deleted)}** messages.")
        await asyncio.sleep(3)
        await msg.delete()
        await send_log(self.bot, "COMMAND", f"Dev purged **{len(deleted)}** messages in <#{ctx.channel.id}>.")

    # ── !maintenance ──────────────────────────────────────────────────────────
    @commands.command(name="maintenance")
    @dev_only()
    async def maintenance(self, ctx: commands.Context, state: str):
        if state.lower() == "on":
            ctx.bot.maintenance = True
            await self.bot.change_presence(
                status   = discord.Status.do_not_disturb,
                activity = discord.Game(name="🔧 Under Maintenance"),
            )
            embed = discord.Embed(
                title       = "🔧 Maintenance Mode",
                description = "The bot iscurrently under maintenance. CFrame willbe back soon!",
                color       = 0xFF6B6B,
            )
            await ctx.send(embed=embed)
            await send_log(self.bot, "STATUS", "Dev turned **maintenance mode ON** — status rotation paused.")

        elif state.lower() == "off":
            ctx.bot.maintenance = False
            await self.bot.change_presence(
                status   = discord.Status.online,
                activity = discord.Game(name="Watching Sai Code"),
            )
            await ctx.send("✅ Maintenance mode **off** — status rotation resumed.")
            await send_log(self.bot, "STATUS", "Dev turned **maintenance mode OFF** — status rotation resumed.")

        else:
            await ctx.send("❌ Use `!maintenance on` or `!maintenance off`.")

    # ── !log ──────────────────────────────────────────────────────────────────
    @commands.command(name="log")
    @dev_only()
    async def log(self, ctx: commands.Context, *, message: str):
        await send_log(self.bot, "STATUS", f"📋 Dev log: {message}")
        await ctx.message.add_reaction("✅")

    # ── !uptime ───────────────────────────────────────────────────────────────
    @commands.command(name="uptime")
    @dev_only()
    async def uptime(self, ctx: commands.Context):
        secs   = int(time.time() - self.start_time)
        h, rem = divmod(secs, 3600)
        m, s   = divmod(rem, 60)
        await ctx.send(f"⏱️ Uptime: **{h}h {m}m {s}s** | Ping: **{round(self.bot.latency * 1000)}ms**")

    # ── !botstats ─────────────────────────────────────────────────────────────
    @commands.command(name="botstats")
    @dev_only()
    async def botstats(self, ctx: commands.Context):
        secs   = int(time.time() - self.start_time)
        h, rem = divmod(secs, 3600)
        m, s   = divmod(rem, 60)
        embed  = discord.Embed(title="🤖 Bot Stats", color=0x5865F2)
        embed.add_field(name="Uptime",      value=f"{h}h {m}m {s}s",                  inline=True)
        embed.add_field(name="Ping",        value=f"{round(self.bot.latency*1000)}ms", inline=True)
        embed.add_field(name="Servers",     value=str(len(self.bot.guilds)),            inline=True)
        embed.add_field(name="Users",       value=str(sum(g.member_count or 0 for g in self.bot.guilds)), inline=True)
        embed.add_field(name="Disabled",    value=str(len(disabled_cmds)) or "None",   inline=True)
        embed.add_field(name="Maintenance", value="🔴 On" if ctx.bot.maintenance else "🟢 Off", inline=True)
        await ctx.send(embed=embed)

    # ── !eval ─────────────────────────────────────────────────────────────────
    @commands.command(name="eval")
    @dev_only()
    async def eval_cmd(self, ctx: commands.Context, *, code: str):
        code = code.strip("` \n").removeprefix("python").removeprefix("py").strip()
        env  = {"bot": self.bot, "ctx": ctx, "discord": discord}
        out  = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                exec(f"async def _ev():\n" + "\n".join(f"    {l}" for l in code.splitlines()), env)
                await eval("_ev()", env)
            result = out.getvalue() or "✅ Done (no output)"
        except Exception:
            result = traceback.format_exc()
        if len(result) > 1900:
            result = result[:1900] + "..."
        await ctx.send(f"```py\n{result}\n```")