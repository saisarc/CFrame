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

status_list = cycle(['Watching Sai Code', 'Playing UC2', 'Listening to Sais Commands'])

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
    change_status.start()
    await bot.add_cog(GameCommands(bot, start_time))
    await bot.add_cog(DevCommands(bot, start_time))
    await bot.add_cog(ExtraCommands(bot))
    await bot.add_cog(Features(bot))
    await bot.add_cog(Music(bot))
    synced = await bot.tree.sync()
    print(f"✅ Bot is online as {bot.user}")
    print(f"🚀 Synced {len(synced)} slash commands.")
    print(f"🛠️  Dev prefix commands loaded (prefix: !)")
    await send_log(bot, "STATUS", f"✅ Bot came **online** as `{bot.user}` — synced {len(synced)} commands.")

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

        bot.run(TOKEN)