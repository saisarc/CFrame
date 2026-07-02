import asyncio
import os
from urllib.parse import urlparse
import collections
from collections import deque
import discord
from discord.ext import commands
import wavelink
import aiohttp
from commands import blocked, send_log


def normalize_query_for_lavalink(query: str) -> str:
    """Normalize a user query so Lavalink/LavaSrc resolves Spotify, Apple Music, and Deezer links correctly."""
    if not query:
        return query

    q = query.strip()
    if not q:
        return q

    if q.lower().startswith(("ytsearch:", "spsearch:", "amsearch:", "dzsearch:")):
        return q

    if q.startswith(("http://", "https://")):
        lowered = q.lower()
        if "spotify.com" in lowered or lowered.startswith("spotify:"):
            return f"spsearch:{q}"
        if "music.apple.com" in lowered or lowered.startswith(("appl:", "apple:")):
            return f"amsearch:{q}"
        if "deezer.com" in lowered or lowered.startswith("dzsearch:"):
            return f"dzsearch:{q}"

    return f"ytsearch:{q}"


def source_to_search_prefix(source: str) -> str:
    mapping = {
        "youtube": "ytsearch",
        "spotify": "spsearch",
        "apple": "amsearch",
        "apple music": "amsearch",
    }
    source_key = (source or "").strip().lower()
    return mapping.get(source_key, "ytsearch")


def spotify_credentials_configured() -> bool:
    return bool(os.getenv("SPOTIFY_CLIENT_ID", "").strip()) and bool(os.getenv("SPOTIFY_CLIENT_SECRET", "").strip())


def make_embed(title: str, description: str, color: int = 0x2b2d31, thumbnail: str = None) -> discord.Embed:
    """Creates a sleek, modern Discord embed matching the native dark theme."""
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="CFrame Music • Lavalink")
    return embed

class SourceSelectionView(discord.ui.View):
    def __init__(self, cog, interaction: discord.Interaction, query: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction
        self.query = query

    async def handle_selection(self, interaction: discord.Interaction, prefix: str):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        if prefix == "spsearch" and not spotify_credentials_configured():
            await self.cog.send_interaction(
                interaction,
                content="⚠️ Spotify search is unavailable on this deployment because Spotify credentials are not configured. Please try YouTube or Apple Music instead.",
                ephemeral=True,
            )
            return

        search_query = f"{prefix}:{self.query}"
        try:
            results = await wavelink.Pool.fetch_tracks(search_query)
            track = None
            if isinstance(results, list):
                track = results[0] if results else None
            else:
                track = results.tracks[0] if getattr(results, "tracks", None) else None

            if not track:
                await self.cog.send_interaction(interaction, content="❌ No audio tracks found for that query.", ephemeral=True)
                return

            await self.cog.process_play_track(interaction, track)
        except Exception as e:
            message = str(e).lower()
            if prefix == "spsearch" and ("forbidden" in message or "403" in message or "something went wrong while looking up the track" in message):
                await self.cog.send_interaction(
                    interaction,
                    content="⚠️ Spotify lookup failed. The current Lavalink setup is not authorized for that search. Please try YouTube or Apple Music instead.",
                    ephemeral=True,
                )
            else:
                await self.cog.send_interaction(interaction, content=f"❌ Error: `{e}`", ephemeral=True)

    @discord.ui.button(label="YouTube", style=discord.ButtonStyle.danger, emoji="🔴")
    async def youtube_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, source_to_search_prefix("youtube"))

    @discord.ui.button(label="Spotify", style=discord.ButtonStyle.success, emoji="🟢")
    async def spotify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, source_to_search_prefix("spotify"))

    @discord.ui.button(label="Apple Music", style=discord.ButtonStyle.secondary, emoji="🍎")
    async def apple_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, source_to_search_prefix("apple music"))

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

        if host.startswith(("http://", "https://", "ws://", "wss://")):
            parsed = urlparse(host)
            scheme = parsed.scheme or "ws"
            if scheme in {"http", "ws"}:
                ws_scheme = "ws"
            elif scheme in {"https", "wss"}:
                ws_scheme = "wss"
            else:
                ws_scheme = "ws"
            uri = f"{ws_scheme}://{parsed.netloc or parsed.path}"
        else:
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

    async def send_interaction(self, interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, ephemeral: bool = False, view: discord.ui.View = None):
        kwargs = {"ephemeral": ephemeral}
        if content:
            kwargs["content"] = content
        if embed:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view

        try:
            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
        except Exception:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                await interaction.followup.send(**kwargs)
            except Exception:
                pass

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

                    if not q:
                        continue

                    next_item = q.popleft()
                    track = next_item.get("track")
                    if not track:
                        continue

                    await player.play(track)

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

    async def process_play_track(self, interaction: discord.Interaction, track: wavelink.Playable):
        try:
            player = await self.ensure_voice(interaction)
        except RuntimeError as e:
            await self.send_interaction(interaction, content=f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await self.send_interaction(interaction, content=f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            return

        guild_id = interaction.guild.id
        q = self.get_queue(guild_id)

        is_playing_fn = getattr(player, "is_playing", None)
        is_paused_fn = getattr(player, "is_paused", None)

        should_play_now = True
        if callable(is_playing_fn):
            try:
                should_play_now = not bool(is_playing_fn())
            except Exception:
                should_play_now = True
        else:
            paused = False
            if callable(is_paused_fn):
                try:
                    paused = bool(is_paused_fn())
                except Exception:
                    paused = False
            should_play_now = not bool(q) and not paused

        item = {
            "track": track,
            "title": getattr(track, "title", "Unknown"),
            "uri": getattr(track, "uri", None),
            "requester_id": interaction.user.id,
            "requester_name": interaction.user.display_name,
        }

        if should_play_now and not q:
            try:
                await player.play(track)
                title = item["title"]
                try:
                    self._get_voice_channel_and_store_original(interaction.guild, player.channel)
                except Exception:
                    pass
                await self._update_voice_status(player, title)
                status_text = "Now playing"
            except Exception as play_error:
                await self.send_interaction(
                    interaction,
                    content="⚠️ That track could not be played by Lavalink. Please try a different query or source.",
                )
                return
        else:
            q.append(item)
            status_text = "Added to queue"

        artwork = getattr(track, "artwork", None)
        author = getattr(track, "author", "Unknown Artist")

        title_text = item.get("title") or "Unknown"
        embed = discord.Embed(
            title=f"🎧 {status_text}" if status_text != "Added to queue" else "📥 Added to Queue",
            description=f"**[{title_text}]({item.get('uri')})**" if item.get("uri") else f"**{title_text}**",
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
            await self.send_interaction(interaction, embed=embed)
        except Exception as response_error:
            print(f"Failed to finish interaction response: {response_error}")
            try:
                await interaction.followup.send(embed=embed)
            except Exception:
                pass

        await send_log(self.bot, "COMMAND", f"{interaction.user} {status_text.lower()}: `{item[title]}`")

    @discord.app_commands.command(name="play", description="Play a song via Lavalink (adds to queue)")
    @discord.app_commands.describe(query="Song name or URL (YouTube, Spotify, Apple Music)")
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
            if query.startswith(("http", "https", "spotify:", "appl:", "apple:")):
                search_query = normalize_query_for_lavalink(query)
            else:
                if query.lower().startswith(("ytsearch:", "spsearch:", "amsearch:", "dzsearch:")):
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
        except Exception as error:
            message = str(error).lower()
            if search_query.startswith("spsearch:") and ("forbidden" in message or "403" in message or "something went wrong while looking up the track" in message):
                await self.send_interaction(
                    interaction,
                    content="⚠️ Spotify lookup failed. The current Lavalink setup is not authorized for that search. Please try YouTube or Apple Music instead.",
                )
            else:
                await self.send_interaction(interaction, content=f"❌ Could not play audio: `{error}`")
            return

        await self.process_play_track(interaction, track)

    @discord.app_commands.command(name="skip", description="Skip the current track")
    @discord.app_commands.describe(reason="Skip reason (optional)")

    async def skip(self, interaction: discord.Interaction, reason: str = None):

        if await blocked(interaction):
            return
        player = interaction.guild.voice_client
        is_playing_fn = getattr(player, "is_playing", None) if player else None
        if not player or not callable(is_playing_fn) or not is_playing_fn():
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

    @discord.app_commands.command(name="deemix", description="Play a Deezer/DeeMix track or search query via Lavalink")
    @discord.app_commands.describe(query="Deezer/DeeMix link or search query")
    async def deemix(self, interaction: discord.Interaction, query: str):
        if await blocked(interaction):
            return

        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        try:
            await self.ensure_lavalink(interaction)
            await self.ensure_voice(interaction)
        except RuntimeError as e:
            await self.send_interaction(interaction, content=f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await self.send_interaction(interaction, content=f"❌ Could not prepare Deezer playback: `{e}`", ephemeral=True)
            return

        try:
            if query.startswith(("http://", "https://")):
                search_query = normalize_query_for_lavalink(query)
            elif query.lower().startswith("dzsearch:"):
                search_query = query
            else:
                search_query = f"dzsearch:{query}"

            results = await wavelink.Pool.fetch_tracks(search_query)
            track = None
            if isinstance(results, list):
                track = results[0] if results else None
            else:
                track = results.tracks[0] if getattr(results, "tracks", None) else None

            if not track:
                await self.send_interaction(interaction, content="❌ No Deezer/DeeMix track found for that query.")
                return
        except Exception as error:
            message = str(error).lower()
            if "master key" in message or "deezer" in message:
                await self.send_interaction(
                    interaction,
                    content="❌ Deezer/DeeMix is not available yet because your Lavalink server is missing a Deezer master key. Add `DEEZER_MASTER_KEY` and re-enable the Deezer source in Lavalink, then restart the service.",
                )
            else:
                await self.send_interaction(interaction, content=f"❌ Could not resolve Deezer playback: `{error}`")
            return

        await self.process_play_track(interaction, track)

    async def _fetch_lavalink_lyrics(self, player: wavelink.Player, track_title: str = None, query: str = None):
        if not await self.is_node_connected():
            return None, None, None

        node = wavelink.Pool.get_node()
        if not node:
            return None, None, None

        http_uri = node.uri.replace("ws://", "http://").replace("wss://", "https://")
        headers = {"Authorization": node.password}
        data = None

        if query:
            search_query = normalize_query_for_lavalink(query)
            results = await wavelink.Pool.fetch_tracks(search_query)
            track = None
            if isinstance(results, list):
                track = results[0] if results else None
            else:
                track = results.tracks[0] if getattr(results, "tracks", None) else None
            if not track:
                return None, None, None
            encoded_track = getattr(track, "encoded", None)
            if not encoded_track:
                return None, None, None
            track_title = getattr(track, "title", track_title or "Unknown Song")
            url = f"{http_uri}/v4/lyrics/{encoded_track}"
        else:
            current_track = getattr(player, "current", None)
            if not current_track:
                return None, None, None
            track_title = getattr(current_track, "title", track_title or "Unknown Song")
            url = f"{http_uri}/v4/sessions/{node.session_id}/players/{player.guild.id}/lyrics"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                    elif resp.status == 204:
                        return None, track_title, None
        except Exception:
            return None, track_title, None

        if not data or not data.get("text"):
            return None, track_title, None
        return data, track_title, None

    @discord.app_commands.command(name="lyrics", description="Get the lyrics of the currently playing song or search for a specific song")
    @discord.app_commands.describe(query="Name of the song to search lyrics for (optional)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        if await blocked(interaction):
            return

        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.channel:
            await self.send_interaction(interaction, content="❌ The bot is not in a voice channel.")
            return

        player = voice_client
        if not isinstance(player, wavelink.Player):
            await self.send_interaction(interaction, content="❌ Voice client is not a valid player.")
            return

        if not query:
            current_track = getattr(player, "current", None)
            if not current_track:
                await self.send_interaction(interaction, content="❌ Nothing is currently playing.")
                return

        data, track_title, _ = await self._fetch_lavalink_lyrics(player, query=query)
        if not data or not data.get("text"):
            await self.send_interaction(interaction, content=f"❌ No lyrics found for **{track_title or 'the requested track'}**.")
            return

        lyrics_text = data["text"]
        source_name = data.get("sourceName", "Unknown Source")
        provider = data.get("provider", "LavaLyrics")

        chunks = []
        current_chunk = ""
        for line in lyrics_text.splitlines():
            if len(current_chunk) + len(line) + 2 > 3500:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk:
            chunks.append(current_chunk)

        for idx, chunk in enumerate(chunks):
            page_title = f"🎤 Lyrics: {track_title}"
            if len(chunks) > 1:
                page_title += f" (Part {idx + 1}/{len(chunks)})"
            
            embed = discord.Embed(
                title=page_title,
                description=chunk,
                color=0x2b2d31
            )
            embed.set_footer(text=f"Source: {source_name} via {provider}")
            
            if idx == 0:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.response.send_message(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

    @discord.app_commands.command(name="filter", description="Apply an audio preset/filter to the active player")
    @discord.app_commands.choices(preset=[
        discord.app_commands.Choice(name="None / Reset", value="reset"),
        discord.app_commands.Choice(name="Nightcore", value="nightcore"),
        discord.app_commands.Choice(name="Slowed", value="slowed"),
        discord.app_commands.Choice(name="Vaporwave", value="vaporwave"),
        discord.app_commands.Choice(name="Bassboost", value="bassboost"),
        discord.app_commands.Choice(name="8D / Rotation", value="8d"),
        discord.app_commands.Choice(name="Karaoke", value="karaoke"),
        discord.app_commands.Choice(name="Tremolo", value="tremolo"),
        discord.app_commands.Choice(name="Vibrato", value="vibrato"),
        discord.app_commands.Choice(name="LowPass", value="lowpass"),
    ])
    async def filter(self, interaction: discord.Interaction, preset: discord.app_commands.Choice[str]):
        if await blocked(interaction):
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ The bot is not in a voice channel.", ephemeral=True)
            return

        player = voice_client
        if not isinstance(player, wavelink.Player):
            await interaction.response.send_message("❌ Voice client is not a valid player.", ephemeral=True)
            return

        await interaction.response.defer()

        filters = getattr(player, "filters", None) or wavelink.Filters()
        filters.reset()
        
        preset_val = preset.value

        if preset_val == "reset":
            await player.set_filters()
            embed = make_embed("🎚️ Filters Reset", "All audio filters have been removed.")
            await interaction.followup.send(embed=embed)
            return

        elif preset_val == "nightcore":
            filters.timescale.set(speed=1.2, pitch=1.25, rate=1.0)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Nightcore Enabled", "Pitch: `1.25x`, Speed: `1.2x`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "slowed":
            filters.timescale.set(speed=0.85, pitch=0.85, rate=1.0)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Slowed Enabled", "Pitch: `0.85x`, Speed: `0.85x`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "vaporwave":
            filters.timescale.set(speed=0.8, pitch=0.8, rate=1.0)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Vaporwave Enabled", "Pitch: `0.80x`, Speed: `0.80x`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "bassboost":
            bands = [
                {"band": 0, "gain": 0.3},
                {"band": 1, "gain": 0.25},
                {"band": 2, "gain": 0.20},
                {"band": 3, "gain": 0.15}
            ]
            filters.equalizer.set(bands=bands)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Bassboost Enabled", "Low frequencies boosted.")
            await interaction.followup.send(embed=embed)

        elif preset_val == "8d":
            filters.rotation.set(rotation_hz=0.2)
            await player.set_filters(filters)
            embed = make_embed("🎚️ 8D Audio Enabled", "Sound panning around stereo channels at `0.2 Hz`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "karaoke":
            filters.karaoke.set(level=1.0, mono_level=1.0, filter_band=220.0, filter_width=100.0)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Karaoke Filter Enabled", "Vocal frequency range attenuated.")
            await interaction.followup.send(embed=embed)

        elif preset_val == "tremolo":
            filters.tremolo.set(frequency=4.0, depth=0.6)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Tremolo Enabled", "Volume oscillation: `4 Hz`, Depth: `0.6`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "vibrato":
            filters.vibrato.set(frequency=4.0, depth=0.6)
            await player.set_filters(filters)
            embed = make_embed("🎚️ Vibrato Enabled", "Pitch oscillation: `4 Hz`, Depth: `0.6`")
            await interaction.followup.send(embed=embed)

        elif preset_val == "lowpass":
            filters.low_pass.set(smoothing=20.0)
            await player.set_filters(filters)
            embed = make_embed("🎚️ LowPass Enabled", "Higher frequencies suppressed (smoothing: `20`)")
            await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Music(bot))

