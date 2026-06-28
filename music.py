import asyncio
from collections import deque

import discord
from discord.ext import commands
from yt_dlp import YoutubeDL

from commands import blocked, send_log

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "noplaylist": True,
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = YoutubeDL(YTDL_OPTIONS)


def make_embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="CFrame Music")
    return embed


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, data, *, volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("webpage_url")
        self.duration = data.get("duration")
        self.uploader = data.get("uploader")

    @classmethod
    async def from_url(cls, query: str, *, loop=None, stream: bool = True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=not stream))
        if "entries" in data:
            data = data["entries"][0]
        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues = {}

    def get_queue(self, guild_id: int) -> deque:
        return self.queues.setdefault(guild_id, deque())

    async def ensure_voice(self, interaction: discord.Interaction) -> discord.VoiceClient:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
            raise RuntimeError("user not in voice channel")

        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client

        if voice_client and voice_client.channel != channel:
            await voice_client.move_to(channel)
        elif not voice_client:
            voice_client = await channel.connect()

        return voice_client

    def next_track(self, guild_id: int):
        queue = self.get_queue(guild_id)
        if not queue:
            self.queues.pop(guild_id, None)
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                asyncio.create_task(guild.voice_client.disconnect())
            return

        source = queue.popleft()
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return

        def after(error):
            if error:
                print(f"Music playback error: {error}")
            fut = asyncio.run_coroutine_threadsafe(self._after_play(guild_id), self.bot.loop)
            try:
                fut.result()
            except Exception:
                pass

        voice_client = guild.voice_client
        voice_client.play(source, after=after)

    async def _after_play(self, guild_id: int):
        queue = self.get_queue(guild_id)
        if queue:
            self.next_track(guild_id)

    @discord.app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        try:
            voice_client = await self.ensure_voice(interaction)
        except RuntimeError:
            return
        except Exception as error:
            # If connecting fails (permissions, network, etc.), report back
            try:
                await interaction.followup.send(f"❌ Could not join voice channel: `{error}`")
            except Exception:
                pass
            return
        await interaction.response.send_message(f"✅ Joined **{voice_client.channel.name}**.")

    @discord.app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ I'm not in a voice channel.", ephemeral=True)
            return
        await voice_client.disconnect()
        self.queues.pop(interaction.guild.id, None)
        await interaction.response.send_message("👋 Disconnected from voice.")

    @discord.app_commands.command(name="play", description="Play a song from YouTube")
    @discord.app_commands.describe(query="YouTube URL or search query")
    async def play(self, interaction: discord.Interaction, query: str):
        if await blocked(interaction):
            return
        await interaction.response.defer()
        try:
            voice_client = await self.ensure_voice(interaction)
        except RuntimeError:
            return

        try:
            source = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
        except Exception as error:
            await interaction.followup.send(f"❌ Could not load audio: `{error}`")
            return

        queue = self.get_queue(interaction.guild.id)
        queue.append(source)

        if not voice_client.is_playing():
            self.next_track(interaction.guild.id)
            await interaction.followup.send(make_embed("▶️ Now playing", f"**{source.title}**\n{source.url}"))
        else:
            await interaction.followup.send(make_embed("➕ Added to queue", f"**{source.title}**\n{source.url}"))

        await send_log(self.bot, "COMMAND", f"{interaction.user} queued music: `{source.title}`")

    @discord.app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        voice_client.stop()
        await interaction.response.send_message("⏭️ Skipped the current track.")

    @discord.app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        voice_client.pause()
        await interaction.response.send_message("⏸️ Paused.")

    @discord.app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_paused():
            await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
            return
        voice_client.resume()
        await interaction.response.send_message("▶️ Resumed playback.")

    @discord.app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        queue = self.get_queue(interaction.guild.id)
        if not queue:
            await interaction.response.send_message("✅ The queue is empty.", ephemeral=True)
            return
        lines = [f"{idx + 1}. {item.title}" for idx, item in enumerate(queue)]
        embed = make_embed("📜 Queue", "\n".join(lines[:10]))
        if len(lines) > 10:
            embed.add_field(name="...", value=f"And {len(lines) - 10} more tracks.")
        await interaction.response.send_message(embed=embed)


a = Music
