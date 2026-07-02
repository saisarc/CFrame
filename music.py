import asyncio
import os
import time
from urllib.parse import urlparse
import collections
from collections import deque
from pathlib import Path
import discord
from discord.ext import commands
import wavelink
import aiohttp
from commands import blocked, send_log

try:
    from deezer import Deezer as DeezerClient
    from deezer import TrackFormats
    from deemix import generateDownloadObject
    from deemix.settings import load as loadDeemixSettings
    from deemix.downloader import Downloader as DeemixDownloader
    from deemix.itemgen import GenerationError as DeemixGenerationError
    DEEMIX_AVAILABLE = True
except ImportError:
    DEEMIX_AVAILABLE = False


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


def preferred_prefix_for_query(query: str) -> str:
    if not query:
        return "amsearch"
    q = query.strip().lower()
    if q.startswith(("spsearch:", "spotify", "https://open.spotify.com", "spotify:")):
        return "spsearch"
    if q.startswith(("amsearch:", "apple music", "music.apple.com", "appl:", "apple:")):
        return "amsearch"
    if q.startswith(("ytsearch:", "youtube", "youtu")):
        return "ytsearch"
    return "amsearch"


def spotify_credentials_configured() -> bool:
    return bool(os.getenv("SPOTIFY_CLIENT_ID", "").strip()) and bool(os.getenv("SPOTIFY_CLIENT_SECRET", "").strip())


def is_deezer_query(query: str) -> bool:
    """Check if a query is a Deezer link or dzsearch prefix."""
    if not query:
        return False
    q = query.strip().lower()
    if q.startswith("dzsearch:"):
        return True
    if q.startswith(("http://", "https://")):
        if "deezer.com" in q:
            return True
    return False


def get_deezer_cache_dir() -> Path:
    """Get or create the Deezer cache directory. Always returns an absolute path."""
    cache_dir = Path(os.getenv("DEEZER_CACHE_DIR", "./deezer_cache")).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


async def search_deezer_tracks(query: str) -> list[dict] | None:
    """
    Search Deezer for tracks using the Deezer API (no auth needed for search).
    Returns list of dicts with {id, title, artist} or None on failure.
    """
    if not query or not query.strip():
        return None

    try:
        url = "https://api.deezer.com/search/track"
        params = {"q": query.strip(), "limit": 5}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("data", [])
                    
                    tracks = []
                    for track in results:
                        tracks.append({
                            "id": track.get("id"),
                            "title": track.get("title", "Unknown"),
                            "artist": track.get("artist", {}).get("name", "Unknown Artist"),
                        })
                    return tracks if tracks else None
        return None
    except Exception as e:
        print(f"Deezer search error: {e}")
        return None


class _DeemixListener:
    """Minimal deemix listener that logs events and captures the saved file path."""
    def __init__(self):
        self.saved_path: str | None = None
        self.completed: list[dict] = []  # track dicts from downloadInfo state=tagged

    def send(self, key, value=None):
        print(f"[Deemix] {key}: {value!r}")
        if isinstance(value, dict):
            # Direct path keys (some deemix versions include these)
            for k in ("path", "filename", "file"):
                if value.get(k):
                    self.saved_path = str(value[k])
            # Capture completed track metadata for fallback filename search
            if key == "downloadInfo" and value.get("state") == "tagged":
                data = value.get("data")
                if isinstance(data, dict):
                    self.completed.append(data)


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

        # Special handling for Deezer search
        if prefix == "deezer":
            try:
                tracks = await search_deezer_tracks(self.query)
                
                if not tracks:
                    await self.cog.send_interaction(
                        interaction,
                        content="❌ No Deezer tracks found for that query. Try another search or source.",
                        ephemeral=True,
                    )
                    return

                # Use the first result
                track_id = tracks[0]["id"]
                
                # Download and play
                file_path, title, artist = await self.cog.download_deezer_track(str(track_id))
                await self.cog.play_via_lavalink_from_file(interaction, file_path, title, artist)

            except Exception as e:
                await self.cog.send_interaction(
                    interaction,
                    content=f"❌ Deezer error: `{e}`",
                    ephemeral=True,
                )
            return

        if prefix == "spsearch" and not spotify_credentials_configured():
            await self.cog.send_interaction(
                interaction,
                content="⚠️ Spotify search is unavailable on this deployment because Lavalink is missing Spotify credentials. Please use Apple Music or a direct URL instead.",
                ephemeral=True,
            )
            return

        if prefix == "amsearch":
            await self.cog.send_interaction(
                interaction,
                content="⚠️ Apple Music search is currently unavailable in this deployment because Lavalink's Apple Music provider is failing. Please use a direct URL or another source.",
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
                    content="⚠️ Spotify lookup failed. Lavalink is missing or rejecting the Spotify credentials. Please use a direct URL or another source.",
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

    @discord.ui.button(label="Deezer", style=discord.ButtonStyle.blurple, emoji="🔵")
    async def deezer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_selection(interaction, "deezer")

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

        # Deezer session cache: track_id (str) -> local file path
        # Avoids re-downloading the same track within a session
        self.deezer_track_cache: dict[str, str] = {}

        # Tracks currently playing via Deezer CDN: guild_id -> (title, artist)
        self.deezer_now_playing: dict[int, tuple[str, str]] = {}
        
        # Deezer tracks "currently loaded" (we called player.play() but is_playing may not have updated)
        # guild_id -> timestamp when track was loaded
        self.deezer_loaded: dict[int, float] = {}

        bot.loop.create_task(self.connect_node())
        bot.loop.create_task(self.queue_worker())

    async def cleanup_ffmpeg_player(self, guild_id: int):
        """Cleanly disconnect active Lavalink player before switching to FFmpeg."""
        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            voice_client = guild.voice_client
            if not voice_client:
                return
            if isinstance(voice_client, wavelink.Player):
                try:
                    await voice_client.stop()
                except Exception:
                    pass
                try:
                    await voice_client.disconnect()
                except Exception:
                    pass
        except Exception:
            pass

    async def download_deezer_track(self, query: str) -> tuple[str, str, str]:
        """
        Download a Deezer track using Deemix.
        query can be a Deezer URL or a numeric track ID string.
        Raises RuntimeError with a descriptive message on any failure.
        Returns: (file_path, track_title, artist_name)
        """
        if not DEEMIX_AVAILABLE:
            raise RuntimeError("deemix is not installed on this server. Check Railway build logs.")

        arl_token = os.getenv("DEEZER_ARL_TOKEN", "").strip()
        if not arl_token:
            raise RuntimeError("`DEEZER_ARL_TOKEN` is not set in environment variables.")

        cache_dir = get_deezer_cache_dir()

        if query.startswith(("http://", "https://")):
            deezer_url = query.split("?")[0]
        else:
            deezer_url = f"https://www.deezer.com/track/{query}"

        # Extract a stable cache key from the track ID in the URL
        try:
            cache_key = deezer_url.rstrip("/").split("/track/")[-1]
        except Exception:
            cache_key = query

        # Return cached file if it still exists on disk
        if cache_key in self.deezer_track_cache:
            cached_path = self.deezer_track_cache[cache_key]
            if Path(cached_path).exists():
                print(f"[Deemix] Cache hit for track {cache_key}: {cached_path}")
                # Re-fetch metadata quickly without re-downloading
                title, artist = "Unknown", "Unknown Artist"
                try:
                    def _get_meta():
                        dz = DeezerClient()
                        dz.login_via_arl(arl_token)
                        t = dz.api.get_track(int(cache_key))
                        return t.get("title", "Unknown"), t.get("artist", {}).get("name", "Unknown Artist")
                    title, artist = await asyncio.to_thread(_get_meta)
                except Exception:
                    pass
                return (cached_path, title, artist)
            else:
                # File was wiped (redeploy), remove stale entry
                del self.deezer_track_cache[cache_key]

        def _do_download():
            import time
            start_ts = time.time()

            dz = DeezerClient()
            if not dz.login_via_arl(arl_token):
                raise RuntimeError("ARL login failed — your token may be expired. Get a new one from deezer.com cookies.")

            title = "Unknown"
            artist = "Unknown Artist"
            try:
                track_id_str = deezer_url.rstrip("/").split("/track/")[-1]
                track_data = dz.api.get_track(int(track_id_str))
                title = track_data.get("title", "Unknown")
                artist = track_data.get("artist", {}).get("name", "Unknown Artist")
            except Exception:
                pass

            settings = loadDeemixSettings()
            settings["downloadLocation"] = str(cache_dir)
            settings["createArtistFolder"] = False
            settings["createAlbumFolder"] = False
            settings["maxBitrate"] = str(TrackFormats.MP3_128)
            print(f"[Deemix] Downloading to: {settings['downloadLocation']}")

            try:
                dl_obj = generateDownloadObject(dz, deezer_url, TrackFormats.MP3_128)
            except DeemixGenerationError as e:
                raise RuntimeError(f"Deezer track could not be resolved: {e}")

            if isinstance(dl_obj, list):
                if not dl_obj:
                    raise RuntimeError("Deezer returned no downloadable object for that track.")
                dl_obj = dl_obj[0]

            listener = _DeemixListener()
            DeemixDownloader(dz, dl_obj, settings, listener).start()

            # Strategy 1: time-based detection with a 3s buffer for clock skew
            def _new_files_in(path: Path):
                result = set()
                try:
                    for f in path.rglob("*"):
                        if f.is_file() and f.stat().st_mtime >= (start_ts - 3):
                            result.add(f)
                except Exception:
                    pass
                return result

            new_files = _new_files_in(cache_dir)

            # Strategy 2: check deemix default fallback locations
            if not new_files:
                for fallback in [
                    Path.home() / "Deemix",
                    Path.home() / "Music",
                    Path("/root/Deemix"),
                    Path("/tmp"),
                ]:
                    if fallback.exists():
                        new_files = _new_files_in(fallback)
                        if new_files:
                            print(f"[Deemix] Found file in fallback dir: {fallback}")
                            break

            # Strategy 3: use listener's captured path directly
            if not new_files and listener.saved_path:
                p = Path(listener.saved_path)
                if p.exists():
                    new_files.add(p)

            # Strategy 4: match by title/artist in filenames across the cache dir
            if not new_files and listener.completed:
                for track_data in listener.completed:
                    t = track_data.get("title", "").lower()
                    a = track_data.get("artist", "").lower()
                    try:
                        for f in cache_dir.rglob("*"):
                            if f.is_file():
                                name = f.name.lower()
                                if t and t in name:
                                    new_files.add(f)
                                    break
                                if a and a in name:
                                    new_files.add(f)
                                    break
                    except Exception:
                        pass

            # Strategy 5: last resort — most recently modified audio file in cache
            if not new_files:
                audio_exts = {".mp3", ".flac", ".m4a", ".ogg", ".opus"}
                candidates = [
                    f for f in cache_dir.rglob("*")
                    if f.is_file() and f.suffix.lower() in audio_exts
                ]
                if candidates:
                    newest = max(candidates, key=lambda f: f.stat().st_mtime)
                    new_files.add(newest)
                    print(f"[Deemix] Using most-recent file as fallback: {newest}")

            if not new_files:
                raise RuntimeError(
                    f"Download ran but no file appeared in `{cache_dir}` or fallback dirs. "
                    "Check Railway logs for [Deemix] events to diagnose."
                )

            return (str(list(new_files)[0]), title, artist)

        try:
            result = await asyncio.to_thread(_do_download)
            # Store in session cache so the same track isn't re-downloaded
            self.deezer_track_cache[cache_key] = result[0]
            return result
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Unexpected download error: {e}")

    async def play_via_lavalink_from_file(self, interaction: discord.Interaction, file_path: str, title: str, artist: str):
        """
        Upload a local audio file to Discord CDN then play via Lavalink.
        Avoids discord.py native voice UDP (blocked on Railway).
        """
        # Upload file to Discord to get a streamable CDN URL
        try:
            f = discord.File(file_path, filename=Path(file_path).name)
            upload_msg = await interaction.channel.send(file=f)
            cdn_url = upload_msg.attachments[0].url
            try:
                await upload_msg.delete()
            except Exception:
                pass
        except Exception as e:
            raise RuntimeError(f"Could not upload file to Discord CDN: {e}")

        # Ensure Lavalink and voice
        await self.ensure_lavalink(interaction)
        try:
            player = await self.ensure_voice(interaction)
        except RuntimeError:
            raise

        # Fetch and play via Lavalink
        print(f"[CDN] Fetching from Lavalink: {cdn_url[:100]}...")
        results = await wavelink.Pool.fetch_tracks(cdn_url)
        print(f"[CDN] Lavalink results type: {type(results)}, results: {results}")
        
        track = None
        if isinstance(results, list):
            print(f"[CDN] Results is list with {len(results)} items")
            track = results[0] if results else None
        else:
            tracks = getattr(results, "tracks", None)
            print(f"[CDN] Results is object with tracks: {tracks}")
            track = tracks[0] if tracks else None

        if not track:
            print(f"[CDN] FAILED: Lavalink could not load the audio from Discord CDN")
            print(f"[CDN] Full results object: {results}")
            raise RuntimeError("Lavalink could not load the audio from Discord CDN.")

        guild_id = interaction.guild.id
        q = self.get_queue(guild_id)
        
        print(f"[Deezer] === NEW /play REQUEST ===")
        print(f"[Deezer] guild_id={guild_id}, guild_name={interaction.guild.name}")
        print(f"[Deezer] PERSISTENT STATE CHECK:")
        print(f"[Deezer]   deezer_loaded dict = {self.deezer_loaded}")
        print(f"[Deezer]   queue length = {len(q)}")
        print(f"[Deezer]   Keys in deezer_loaded: {list(self.deezer_loaded.keys())}")
        if guild_id in self.deezer_loaded:
            elapsed = time.time() - self.deezer_loaded[guild_id]
            print(f"[Deezer]   ⚠️  Guild HAS deezer_loaded! Elapsed: {elapsed:.2f}s")

        is_playing_fn = getattr(player, "is_playing", None)
        is_paused_fn = getattr(player, "is_paused", None)

        should_play_now = True
        if callable(is_playing_fn):
            try:
                should_play_now = not bool(is_playing_fn())
                print(f"[Deezer] is_playing()={is_playing_fn()}, should_play_now={should_play_now}")
            except Exception as e:
                print(f"[Deezer] is_playing() error: {e}")
                should_play_now = True
        else:
            paused = False
            if callable(is_paused_fn):
                try:
                    paused = bool(is_paused_fn())
                    print(f"[Deezer] is_paused()={paused}")
                except Exception as e:
                    print(f"[Deezer] is_paused() error: {e}")
                    paused = False
            should_play_now = not bool(q) and not paused
            print(f"[Deezer] Queue logic: queue_len={len(q)}, paused={paused}, should_play_now={should_play_now}")

        # Check if should queue: look at is_playing(), is_paused(), queue, OR deezer_loaded flag
        current_time = time.time()
        
        # Strategy 1: Check deezer_loaded flag (tracks what we just played)
        deezer_recently_loaded = False
        if guild_id in self.deezer_loaded:
            time_since_load = current_time - self.deezer_loaded[guild_id]
            deezer_recently_loaded = time_since_load < 5.0
            print(f"[Deezer] Strategy 1 - Recently loaded: time_since_load={time_since_load:.2f}s, within_5s={deezer_recently_loaded}")
        
        # Strategy 2: Check current track URI (if Lavalink has already loaded it)
        current_track = getattr(player, "current", None)
        is_cdn_playing = False
        if current_track:
            current_uri = getattr(current_track, "uri", "") or ""
            is_cdn_playing = "cdn.discordapp.com" in current_uri or "media.discordapp.net" in current_uri
            print(f"[Deezer] Strategy 2 - Current track URI check: is_cdn={is_cdn_playing}")
        
        # If EITHER strategy says something is playing, queue instead of playing
        should_queue_not_play = deezer_recently_loaded or is_cdn_playing
        if should_queue_not_play:
            print(f"[Deezer] ✓ Deezer track detected (loaded or playing), will QUEUE this next song")
            should_play_now = False

        if should_play_now and not q:
            print(f"[Deezer] BEFORE player.play(): deezer_loaded keys = {list(self.deezer_loaded.keys())}")
            await player.play(track)
            self.deezer_loaded[guild_id] = time.time()  # Mark as loaded
            print(f"[Deezer] AFTER player.play(): set deezer_loaded[{guild_id}] = {self.deezer_loaded[guild_id]}")
            self.deezer_now_playing[interaction.guild.id] = (title, artist)
            status = "Now playing"
            print(f"[Deezer] Playing immediately: {title}")
        else:
            print(f"[Deezer] Should queue: should_play_now={should_play_now}, queue_len={len(q)}")
            q.append({
                "track": track,
                "title": title,
                "artist": artist,
                "uri": cdn_url,
                "requester_id": interaction.user.id,
                "requester_name": interaction.user.display_name,
            })
            status = "Added to queue"
            print(f"[Deezer] Queued: {title} (queue now has {len(q) + 1} items)")

        embed = discord.Embed(
            title="🎧 Now playing" if status != "Added to queue" else "📥 Added to Queue",
            description=f"**{title}**",
            color=0x2b2d31,
        )
        embed.add_field(name="Channel / Artist", value=f"`{artist}`", inline=True)
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name} • Via Deezer",
            icon_url=interaction.user.display_avatar.url,
        )
        await self.send_interaction(interaction, embed=embed)
        await send_log(self.bot, "COMMAND", f"{status} (Deezer): `{title}` by {artist}")






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
                    artist = next_item.get("artist") or getattr(track, "author", "Unknown Artist")
                    
                    # Clear deezer_loaded flag since track is now actually playing
                    if guild_id in self.deezer_loaded:
                        del self.deezer_loaded[guild_id]
                    
                    # If this was a Deezer track, update now-playing metadata for lyrics
                    if "artist" in next_item:
                        self.deezer_now_playing[guild_id] = (title, artist)
                    
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
                # Check if the track is actually playing despite the exception
                is_playing = False
                try:
                    is_playing_fn = getattr(player, "is_playing", None)
                    if callable(is_playing_fn):
                        is_playing = bool(is_playing_fn())
                except Exception:
                    pass
                
                # If track is playing, ignore the exception - it's just a warning
                if is_playing:
                    title = item["title"]
                    try:
                        self._get_voice_channel_and_store_original(interaction.guild, player.channel)
                    except Exception:
                        pass
                    await self._update_voice_status(player, title)
                    status_text = "Now playing"
                else:
                    # Track is not playing, show the error
                    message = str(play_error).lower()
                    if "requires login" in message or "player configuration error" in message or "all clients failed" in message:
                        await self.send_interaction(
                            interaction,
                            content="⚠️ That YouTube result could not be played. The video may be age-restricted, region-locked, or require login. Please try a different song or source.",
                        )
                    else:
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

    @discord.app_commands.command(name="play", description="Play a song via Lavalink (adds to queue) or Deezer via Deemix")
    @discord.app_commands.describe(query="Song name or URL (YouTube, Spotify, Apple Music, or Deezer)")
    async def play(self, interaction: discord.Interaction, query: str):
        if await blocked(interaction):
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        # Check if this is a Deezer query
        if is_deezer_query(query):
            # === DEEZER PATH: Download via Deemix + play via FFmpeg ===
            try:
                file_path, title, artist = await self.download_deezer_track(query)
                await self.play_via_lavalink_from_file(interaction, file_path, title, artist)
            except Exception as e:
                await self.send_interaction(
                    interaction,
                    content=f"❌ Deezer playback error: `{e}`",
                    ephemeral=True,
                )
            return

        # === LAVALINK PATH: Existing logic for YouTube, Spotify, Apple Music ===
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
                    view = SourceSelectionView(self, interaction, query)
                    embed = discord.Embed(
                        title="🎵 Choose a source",
                        description=f"Select where to search for **{query}**.",
                        color=0x2b2d31,
                    )
                    embed.add_field(name="Options", value="YouTube • Spotify • Apple Music • Deezer", inline=False)
                    await self.send_interaction(interaction, embed=embed, ephemeral=True, view=view)
                    return

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

            # If playing a Deezer CDN track, use stored title as search query
            current_uri = getattr(current_track, "uri", "") or ""
            print(f"[Lyrics] Current track URI: {current_uri[:100] if current_uri else 'None'}")
            
            if "cdn.discordapp.com" in current_uri or "media.discordapp.net" in current_uri or "discordapp.net" in current_uri:
                deezer_meta = self.deezer_now_playing.get(interaction.guild.id)
                print(f"[Lyrics] CDN URL detected. Deezer metadata: {deezer_meta}")
                
                if deezer_meta:
                    query = f"{deezer_meta[0]} {deezer_meta[1]}"
                    print(f"[Lyrics] Using Deezer search query: {query}")
                else:
                    await self.send_interaction(interaction, content="❌ Could not determine the current Deezer track for lyrics lookup.")
                    return
            else:
                print(f"[Lyrics] Using current track from Lavalink")

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

