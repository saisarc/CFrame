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

        # guild_id -> deque[ dict(track=..., title=..., uri=..., requester_id=..., requester_name=...) ]
        self.queues: dict[int, deque] = {}

        # guild_id -> player state
        # { voice_channel_name: original name, voice_status_enabled: bool, voice_status_suffix: str }
        self.voice_status: dict[int, dict] = {}

        # config
        self.repeat_modes = {}  # guild_id -> 'off'|'one'|'all'
        self.volumes = {}       # guild_id -> int

        bot.loop.create_task(self.connect_node())
        bot.loop.create_task(self.queue_worker())


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

    def _get_voice_channel_and_store_original(self, guild: discord.Guild, voice_channel: discord.VoiceChannel):
        st = self.voice_status.setdefault(guild.id, {})
        if "original_name" not in st:
            st["original_name"] = voice_channel.name
        if "voice_status_enabled" not in st:
            st["voice_status_enabled"] = False
        if "voice_status_suffix" not in st:
            st["voice_status_suffix"] = "🎵 {title}"
        return st

    async def _update_voice_status(self, player: wavelink.Player, track_title: str):
        guild = player.guild
        voice_client = guild.voice_client
        if not voice_client or not voice_client.channel:
            return

        st = self.voice_status.get(guild.id)
        if not st or not st.get("voice_status_enabled"):
            return

        channel = voice_client.channel
        suffix = st.get("voice_status_suffix") or "🎵 {title}"
        # Discord channel name limit is 100 characters.
        new_name = f"{st.get('original_name', channel.name)} | {suffix.replace('{title}', track_title)}"
        if len(new_name) > 100:
            new_name = new_name[:97] + "..."

        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except Exception:
            pass

    async def _restore_voice_status(self, guild: discord.Guild):
        st = self.voice_status.get(guild.id)
        if not st:
            return
        voice_client = guild.voice_client
        if not voice_client or not voice_client.channel:
            return
        original = st.get("original_name")
        if not original:
            return
        try:
            if voice_client.channel.name != original:
                await voice_client.channel.edit(name=original)
        except Exception:
            pass

    async def queue_worker(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild_id, q in list(self.queues.items()):
                    guild = self.bot.get_guild(guild_id)
                    if not guild or not guild.voice_client:
                        continue
                    player = guild.voice_client
                    if not isinstance(player, wavelink.Player):
                        continue

                    # wavelink.Player API differs by version; only use methods if they exist.
                    is_playing = getattr(player, "is_playing", None)
                    if callable(is_playing):
                        try:
                            if player.is_playing():
                                continue
                        except Exception:
                            pass

                    is_paused = getattr(player, "is_paused", None)
                    if callable(is_paused):
                        try:
                            if player.is_paused():
                                continue
                        except Exception:
                            pass

                    # If nothing is queued, do nothing.
                    if not q:
                        # repeat-all doesn't apply unless we have something to repeat.
                        continue

                    next_item = q.popleft()
                    track = next_item.get("track")
                    if not track:
                        continue

                    await player.play(track)

                    # update status once playback starts
                    title = next_item.get("title") or getattr(track, "title", "Unknown")
                    await self._update_voice_status(player, title)
                    await send_log(self.bot, "COMMAND", f"Now playing (queue): `{title}`")

            except Exception:
                pass

            await asyncio.sleep(1)


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

    @discord.app_commands.command(name="play", description="Play a song via Lavalink (adds to queue)")
    @discord.app_commands.describe(query="YouTube URL/search, or an audio URL/URI supported by your Lavalink plugins")
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
            
        # Resolve track via Lavalink.
        try:
            if query.startswith("http") or query.startswith("https"):
                search_query = query
            elif query.lower().startswith("ytsearch:"):
                search_query = query
            elif query.lower().startswith(("spotify:", "appl:", "apple:", "soundcloud:", "bandcamp:", "twitch:")):
                # Pass-through for lavalink plugins that support these URL schemes.
                search_query = query

            else:

                # Default: YouTube search
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
        except Exception as error:
            await self.send_interaction(interaction, content=f"❌ Could not play audio: `{error}`")
            return

        guild_id = interaction.guild.id
        q = self.get_queue(guild_id)

        # If the bot is not currently playing, play immediately; otherwise enqueue.
        # wavelink.Player API differs by version; avoid calling methods that don't exist.
        is_playing = getattr(player, "is_playing", None)
        should_play_now = True
        if callable(is_playing):
            try:
                should_play_now = not bool(is_playing())
            except Exception:
                should_play_now = True

        item = {
            "track": track,
            "title": getattr(track, "title", "Unknown"),
            "uri": getattr(track, "uri", None),
            "requester_id": interaction.user.id,
            "requester_name": interaction.user.display_name,
        }

        if should_play_now and not q:
            await player.play(track)
            title = item["title"]
            # store original channel name if voice status is enabled
            try:
                self._get_voice_channel_and_store_original(interaction.guild, player.channel)
            except Exception:
                pass
            await self._update_voice_status(player, title)
            status_text = "Now playing"
        else:
            q.append(item)
            status_text = "Added to queue"

        # UI Polish: rich card
        artwork = getattr(track, "artwork", None)
        author = getattr(track, "author", "Unknown Artist")

        embed = discord.Embed(
            title=f"🎧 {status_text}" if status_text != "Added to queue" else "📥 Added to Queue",
            description=f"**[{item['title']}]({item['uri']})**" if item.get("uri") else f"**{item['title']}**",
            color=0x2b2d31,
        )
        if artwork:
            embed.set_thumbnail(url=artwork)

        embed.add_field(name="Channel / Artist", value=f"`{author}`", inline=True)
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url,
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

        await send_log(self.bot, "COMMAND", f"{interaction.user} {status_text.lower()}: `{item['title']}`")


    @discord.app_commands.command(name="skip", description="Skip the current track")
    @discord.app_commands.describe(reason="Skip reason (optional)")

    async def skip(self, interaction: discord.Interaction, reason: str = None):

        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        is_playing = getattr(player, "is_playing", None) if player else None
        if not player or not callable(is_playing) or not is_playing():
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
            return
        try:
            await player.stop()
        except Exception:
            # ignore stop errors, queue worker will handle next
            pass
        embed = make_embed(
            "⏭️ Track Skipped",
            f"Skipped. {('Reason: ' + reason) if reason else ''}".strip(),
        )
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

    @discord.app_commands.command(name="voiceupdate", description="Auto-rename the bot's connected voice channel to match the current song")
    @discord.app_commands.describe(suffix="Rename template. Use {title} for the song title")
    async def voiceupdate(self, interaction: discord.Interaction, suffix: str = "🎵 {title}"):
        if await blocked(interaction):
            return

        # Require bot to be in voice
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.channel:
            await interaction.response.send_message("❌ I'm not connected to a voice channel.", ephemeral=True)
            return

        # store original name
        st = self._get_voice_channel_and_store_original(interaction.guild, voice_client.channel)
        st["voice_status_enabled"] = True
        if suffix:
            st["voice_status_suffix"] = suffix
        await self._restore_voice_status(interaction.guild)  # normalize before applying

        # if currently playing, update immediately
        try:
            if voice_client.is_playing():
                title = getattr(getattr(voice_client, "track", None), "title", None) or "Unknown"
                await self._update_voice_status(voice_client, title)
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ Voice channel status updates enabled. Template: `{suffix}`",
            ephemeral=True,
        )

    @discord.app_commands.command(name="voiceupdatedisable", description="Disable auto-rename and restore the original voice channel name")
    async def voiceupdatedisable(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        if interaction.guild.id not in self.voice_status:
            await interaction.response.send_message("✅ Voice channel status is already disabled.", ephemeral=True)
            return

        st = self.voice_status[interaction.guild.id]
        st["voice_status_enabled"] = False
        await self._restore_voice_status(interaction.guild)
        await interaction.response.send_message("✅ Restored original voice channel name.", ephemeral=True)

    @discord.app_commands.command(name="queue", description="Show the current music queue")

    async def queue(self, interaction: discord.Interaction):

        if await blocked(interaction):
            return
        queue = self.get_queue(interaction.guild.id)
        if not queue:
            await interaction.response.send_message("☕ **The queue is completely empty.**", ephemeral=True)
            return
            
        lines = []
        for idx, item in enumerate(queue):
            title = item.get("title") or getattr(item.get("track"), "title", "Unknown")
            lines.append(f"`{idx + 1}.` **{title}**")

        embed = make_embed("📜 Upcoming Tracks", "\n".join(lines[:10]))
        
        if len(lines) > 10:
            embed.add_field(name="Remaining", value=f"*...and {len(lines) - 10} more tracks waiting.*", inline=False)
            
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Music(bot))
