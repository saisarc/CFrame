import os
import time
import discord
from discord.ext import commands, tasks
from itertools import cycle
from dotenv import load_dotenv
from commands import GameCommands, send_log
from dev_commands import DevCommands
from extra_commands import ExtraCommands
from features import Features
from music import Music

load_dotenv()
TOKEN      = os.getenv("DISCORD_TOKEN")
start_time = time.time()

status_list = cycle(['Watching beeping booping', 'Playing in developer mode', 'Playing My creators favorite child'])

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.maintenance = False

@tasks.loop(seconds=10)
async def change_status():
    if not bot.maintenance:
        await bot.change_presence(activity=discord.Game(name=next(status_list)))

@bot.event
async def on_ready():
    # Start status task and add cogs with per-cog error handling so we can
    # see which cog (if any) causes startup to fail without the whole process exiting.
    change_status.start()

    async def safe_add_cog(cog_ctor, *args):
        name = getattr(cog_ctor, '__name__', str(cog_ctor))
        print(f"Starting add_cog: {name}")
        try:
            await bot.add_cog(cog_ctor(*args))
            print(f"Finished add_cog: {name}")
        except Exception:
            import traceback
            tb = traceback.format_exc()
            print(f"Exception while adding cog {name}:\n{tb}")
            try:
                await send_log(bot, "CRASH", f"💥 Exception while adding cog {name}:\n```{tb[:1000]}```", error=True)
            except Exception:
                pass

    await safe_add_cog(GameCommands, bot, start_time)
    await safe_add_cog(DevCommands, bot, start_time)
    await safe_add_cog(ExtraCommands, bot)
    await safe_add_cog(Features, bot)
    await safe_add_cog(Music, bot)

    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot is online as {bot.user}")
        print(f"🚀 Synced {len(synced)} slash commands.")
        print(f"🛠️  Dev prefix commands loaded (prefix: !)")
        try:
            await send_log(bot, "STATUS", f"✅ Bot came **online** as `{bot.user}` — synced {len(synced)} commands.")
        except Exception:
            pass
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print("Exception while syncing commands:\n" + tb)
        try:
            await send_log(bot, "CRASH", f"💥 Exception while syncing commands:\n```{tb[:1000]}```", error=True)
        except Exception:
            pass

@bot.event
async def on_disconnect():
    await send_log(bot, "RESTART", "⚠️ Bot **disconnected** from Discord.")

@bot.event
async def on_resumed():
    await send_log(bot, "RESTART", "🔄 Bot **reconnected** and resumed session.")

@bot.event
async def on_error(event, *args, **kwargs):
    import traceback
    err = traceback.format_exc()
    # Print traceback to stdout so platform logs capture it as well
    print(f"Unhandled error in {event}:\n{err}")
    try:
        await send_log(bot, "CRASH", f"💥 Unhandled error in `{event}`:\n```{err[:1000]}```", error=True)
    except Exception:
        pass

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: DISCORD_TOKEN missing from .env")
    else:
        import shutil, subprocess
        print("Connecting to Discord...")
        # Log ffmpeg availability for deployment diagnostics
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            try:
                out = subprocess.check_output([ffmpeg_path, "-version"], stderr=subprocess.STDOUT, text=True)
                print("ffmpeg detected:")
                print(out.splitlines()[0])
            except Exception as e:
                print(f"ffmpeg present but failed to run: {e}")
        else:
            print("ffmpeg: NOT FOUND")

        try:
            bot.run(TOKEN)
        except Exception:
            import traceback, time
            tb = traceback.format_exc()
            print("Top-level exception while running bot:\n" + tb)
            # persist debug log so platform logs can surface it or you can download it
            try:
                with open('/tmp/cframe-debug.log', 'w') as f:
                    f.write(tb)
            except Exception:
                pass
            # keep container alive for a while so logs are accessible
            print("Bot crashed — sleeping for 10 minutes to allow log inspection.")
            time.sleep(600)
