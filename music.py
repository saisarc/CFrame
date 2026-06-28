import os
from collections import deque

import discord
from discord.ext import commands
import wavelink

from commands import blocked, send_log


def make_embed(title: str, description: str, color: int = 0x5865F2) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="CFrame Music (Lavalink)")
    return embed


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues = {}
        bot.loop.create_task(self.connect_node())

    async def connect_node(self):
        await self.bot.wait_until_ready()
        host = os.getenv("LAVALINK_HOST", "127.0.0.1")
        port = int(os.getenv("LAVALINK_PORT", "2333"))
        password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
        try:
            await wavelink.NodePool.create_node(bot=self.bot, host=host, port=port, password=password)
            print(f"Connected to Lavalink node at {host}:{port}")
        except Exception as e:
            print(f"Failed to connect to Lavalink node: {e}")

    def get_queue(self, guild_id: int):
        return self.queues.setdefault(guild_id, deque())

    async def ensure_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Join a voice channel first.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Join a voice channel first.", ephemeral=True)
            except Exception:
                pass
            raise RuntimeError("user not in voice channel")

        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client
        if not voice_client:
            # connect and use Wavelink player
            player = await channel.connect(cls=wavelink.Player)
            return player
        else:
            # move if different channel
            if voice_client.channel != channel:
                await voice_client.move_to(channel)
            return voice_client

    @discord.app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        try:
            player = await self.ensure_voice(interaction)
        except RuntimeError:
            return
        except Exception as e:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            return

        if interaction.response.is_done():
            await interaction.followup.send(f"✅ Joined **{player.channel.name}**")
        else:
            await interaction.response.send_message(f"✅ Joined **{player.channel.name}**")

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

    @discord.app_commands.command(name="play", description="Play a song via Lavalink")
    @discord.app_commands.describe(query="YouTube URL or search query")
    async def play(self, interaction: discord.Interaction, query: str):
        if await blocked(interaction):
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        try:
            player = await self.ensure_voice(interaction)
        except RuntimeError:
            return

        try:
            # search via wavelink
            track = await wavelink.YouTubeTrack.search(query, return_first=True)
            if not track:
                await interaction.followup.send("❌ No results found.")
                return
            await player.play(track)
        except Exception as error:
            await interaction.followup.send(f"❌ Could not play audio: `{error}`")
            return

        if interaction.response.is_done():
            await interaction.followup.send(make_embed("▶️ Now playing", f"**{track.title}**\n{track.uri}"))
        else:
            await interaction.response.send_message(make_embed("▶️ Now playing", f"**{track.title}**\n{track.uri}"))

        await send_log(self.bot, "COMMAND", f"{interaction.user} queued music: `{track.title}`")

    @discord.app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_playing():
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        await player.stop()
        await interaction.response.send_message("⏭️ Skipped the current track.")

    @discord.app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_playing():
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        await player.pause()
        await interaction.response.send_message("⏸️ Paused.")

    @discord.app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_paused():
            await interaction.response.send_message("❌ Nothing is paused.", ephemeral=True)
            return
        await player.resume()
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
