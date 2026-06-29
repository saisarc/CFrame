import asyncio
import os
import collections
from collections import deque
import discord
from discord.ext import commands
import wavelink
from commands import blocked, send_log

def make_embed(title: str, description: str, color: int = 0x2b2d31, thumbnail: str = None) -> discord.Embed:
    """Creates a sleek, modern Discord embed matching the native dark theme."""
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="CFrame Music • Lavalink")
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
        
        uri = f"ws://{host}:{port}"
        
        while True:
            try:
                node = wavelink.Node(uri=uri, password=password)
                await wavelink.Pool.connect(client=self.bot, nodes=[node])
                print(f"Connected to Lavalink node at {uri}")
                return
            except Exception as e:
                print(f"Failed to connect to Lavalink node: {e}")
                await asyncio.sleep(5)

    async def is_node_connected(self) -> bool:
        try:
            node = wavelink.Pool.get_node()
            return bool(node and getattr(node, "connected", False))
        except Exception:
            return False

    async def send_interaction(self, interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, ephemeral: bool = False):
        kwargs = {"ephemeral": ephemeral}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed

        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)

    async def ensure_lavalink(self, interaction: discord.Interaction):
        if await self.is_node_connected():
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.connect_node()
       
    def get_queue(self, guild_id: int):
        return self.queues.setdefault(guild_id, deque())

    async def ensure_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            try:
                msg = "❌ You must join a voice channel first."
                if not interaction.response.is_done():
                    await interaction.response.send_message(msg, ephemeral=True)
                else:
                    await interaction.followup.send(msg, ephemeral=True)
            except Exception:
                pass
            raise RuntimeError("user not in voice channel")
            
        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client
        if not voice_client:
            player = await channel.connect(cls=wavelink.Player)
            return player
        else:
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
            await self.ensure_lavalink(interaction)
            player = await self.ensure_voice(interaction)
        except RuntimeError as e:
            await self.send_interaction(interaction, content=f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await self.send_interaction(interaction, content=f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            return
            
        embed = make_embed("🔊 Connected", f"Bound to voice channel **{player.channel.name}**")
        await self.send_interaction(interaction, embed=embed)

    @discord.app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ I'm currently not in a voice channel.", ephemeral=True)
            return
        await voice_client.disconnect()
        self.queues.pop(interaction.guild.id, None)
        
        embed = make_embed("👋 Disconnected", "Successfully cleared the queue and left the channel.")
        await interaction.response.send_message(embed=embed)

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
            await self.ensure_lavalink(interaction)
            player = await self.ensure_voice(interaction)
        except RuntimeError as e:
            await self.send_interaction(interaction, content=f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await self.send_interaction(interaction, content=f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            return
            
        try:
            if query.startswith("http") or query.startswith("https"):
                search_query = query
            elif query.lower().startswith("ytsearch:"):
                search_query = query
            else:
                search_query = f"ytsearch:{query}"
                
            results = await wavelink.Pool.fetch_tracks(search_query)
            track = None
            if isinstance(results, list):
                track = results[0] if results else None
            else:
                track = results.tracks[0] if getattr(results, "tracks", None) else None
                
            if not track:
                await self.send_interaction(interaction, content="❌ No audio tracks found for that query.")
                return
                
            await player.play(track)
        except Exception as error:
            await self.send_interaction(interaction, content=f"❌ Could not play audio: `{error}`")
            return
            
        # UI Polish: Build a rich 'Now Playing' card
        artwork = getattr(track, "artwork", None)
        author = getattr(track, "author", "Unknown Artist")
        
        embed = discord.Embed(
            title="🎧 Now Playing",
            description=f"**[{track.title}]({track.uri})**",
            color=0x2b2d31
        )
        if artwork:
            embed.set_thumbnail(url=artwork)
            
        embed.add_field(name="Channel / Artist", value=f"`{author}`", inline=True)
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name}", 
            icon_url=interaction.user.display_avatar.url
        )
        
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)
        except Exception as response_error:
            print(f"Failed to finish interaction response: {response_error}")
            try:
                await interaction.followup.send(embed=embed)
            except Exception:
                pass
                
        await send_log(self.bot, "COMMAND", f"{interaction.user} queued music: `{track.title}`")

    @discord.app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_playing():
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
            return
        await player.stop()
        embed = make_embed("⏭️ Track Skipped", "Skipped to the next available track in the queue.")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_playing():
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        await player.pause()
        embed = make_embed("⏸️ Paused", "Playback has been suspended. Use `/resume` to continue.")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        if not player or not player.is_paused():
            await interaction.response.send_message("❌ The player is not paused.", ephemeral=True)
            return
        await player.resume()
        embed = make_embed("▶️ Resumed", "Audio playback has resumed.")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        queue = self.get_queue(interaction.guild.id)
        if not queue:
            await interaction.response.send_message("☕ **The queue is completely empty.**", ephemeral=True)
            return
            
        lines = [f"`{idx + 1}.` **{item.title}**" for idx, item in enumerate(queue)]
        embed = make_embed("📜 Upcoming Tracks", "\n".join(lines[:10]))
        
        if len(lines) > 10:
            embed.add_field(name="Remaining", value=f"*...and {len(lines) - 10} more tracks waiting.*", inline=False)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Music(bot))
