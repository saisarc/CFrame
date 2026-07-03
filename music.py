import asyncio
import os
import time
import re
from urllib.parse import urlparse
import collections
from collections import deque
from pathlib import Path
from html import unescape
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
    Returns list of dicts with {id, title, artist, album_art} or None on failure.
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
                        album = track.get("album", {})
                        tracks.append({
                            "id": track.get("id"),
                            "title": track.get("title", "Unknown"),
                            "artist": track.get("artist", {}).get("name", "Unknown Artist"),
                            "album_art": album.get("cover_xl") or album.get("cover_big") or album.get("cover"),
                        })
                    return tracks if tracks else None
        return None
    except Exception as e:
        print(f"Deezer search error: {e}")
        return None


class _DeemixListener:
    """Minimal deemix listener that logs events and captures the saved file path and album art."""
    def __init__(self):
        self.saved_path: str | None = None
        self.album_art_url: str | None = None
        self.completed: list[dict] = []  # track dicts from downloadInfo state=tagged

    def send(self, key, value=None):
        print(f"[Deemix] {key}: {value!r}")
        if isinstance(value, dict):
            # Capture downloadPath from updateQueue events (most reliable, only if not already set)
            if key == "updateQueue" and value.get("downloaded") is True and not self.saved_path:
                path = value.get("downloadPath")
                if path:
                    self.saved_path = str(path)
                    print(f"[Deemix] Captured downloadPath: {path}")
            
            # Capture album art URL from downloadInfo metadata
            if key == "downloadInfo" and not self.album_art_url:
                data = value.get("data")
                if isinstance(data, dict):
                    # Try common album art fields in Deemix metadata
                    for art_field in ("picUrl", "picture", "cover", "coverUrl", "albumArt"):
                        if data.get(art_field):
                            self.album_art_url = str(data[art_field])
                            print(f"[Deemix] Captured album art: {art_field}")
                            break
            
            # Direct path keys (fallback only if downloadPath not captured)
            if not self.saved_path:
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
        self.active_filter_presets: dict[int, str] = {}

        # Deezer session cache: track_id (str) -> local file path
        # Avoids re-downloading the same track within a session
        self.deezer_track_cache: dict[str, str] = {}

        # Tracks currently playing via Deezer CDN: guild_id -> (title, artist)
        self.deezer_now_playing: dict[int, tuple[str, str]] = {}

        # Track active players per guild to support simultaneous music playback
        self.active_players: dict[int, wavelink.Player] = {}

        # Track last playing track per guild to detect track changes
        self.last_playing_track: dict[int, str] = {}  # guild_id -> track.identifier
        self.idle_since: dict[int, float] = {}          # guild_id -> timestamp when idle started
        self._last_text_channel: dict[int, int] = {}    # guild_id -> channel_id for idle messages
        self._progress_last_update_at: dict[int, float] = {}
        self._progress_last_payload: dict[int, tuple[str, str]] = {}
        self._progress_min_interval = float(os.getenv("PROGRESS_EDIT_MIN_INTERVAL", "1.0"))

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

    async def cleanup_idle_connections(self):
        """Disconnect from voice channels that have been idle (no one playing) for 5+ minutes."""
        try:
            import time
            current_time = time.time()
            
            for guild_id in list(self.active_players.keys()):
                try:
                    guild = self.bot.get_guild(guild_id)
                    if not guild or not guild.voice_client:
                        self.active_players.pop(guild_id, None)
                        continue
                    
                    player = guild.voice_client
                    if not isinstance(player, wavelink.Player):
                        continue
                    
                    # Check if nothing is playing
                    is_playing = False
                    try:
                        is_playing = player.playing or player.paused
                    except Exception:
                        pass
                    
                    if not is_playing:
                        # Check if idle for too long (estimate: if queue empty and nothing current)
                        queue = getattr(player, "queue", None)
                        current = getattr(player, "current", None)
                        if not current and (not queue or len(queue) == 0):
                            print(f"[Cleanup] Disconnecting idle player from guild {guild_id}")
                            await player.disconnect()
                            self.active_players.pop(guild_id, None)
                            self.queues.pop(guild_id, None)
                except Exception as e:
                    print(f"[Cleanup] Error checking guild {guild_id}: {e}")
        except Exception as e:
            print(f"[Cleanup] Error in cleanup_idle_connections: {e}")

    async def cleanup_deezer_cache(self):
        """Remove Deezer cache files older than 24 hours to save disk space."""
        try:
            import time
            cache_dir = get_deezer_cache_dir()
            current_time = time.time()
            max_age = 86400  # 24 hours in seconds
            
            removed_count = 0
            removed_size = 0
            
            for file_path in cache_dir.glob("*.mp3"):
                try:
                    file_age = current_time - file_path.stat().st_mtime
                    if file_age > max_age:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        removed_count += 1
                        removed_size += file_size
                        
                        # Also remove from cache dict
                        track_id = str(file_path.stem).split(" - ")[-1] if " - " in file_path.stem else None
                        if track_id:
                            self.deezer_track_cache.pop(track_id, None)
                except Exception as e:
                    print(f"[Cleanup] Could not remove {file_path}: {e}")
            
            if removed_count > 0:
                size_mb = removed_size / 1024 / 1024
                print(f"[Cleanup] Removed {removed_count} old Deezer cache files ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"[Cleanup] Error in cleanup_deezer_cache: {e}")

    async def download_deezer_track(self, query: str) -> tuple[str, str, str, str | None]:
        """
        Download a Deezer track using Deemix.
        query can be a Deezer URL or a numeric track ID string.
        Raises RuntimeError with a descriptive message on any failure.
        Returns: (file_path, track_title, artist_name, album_art_url)
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
                return (cached_path, title, artist, None)
            else:
                # File was wiped (redeploy), remove stale entry
                del self.deezer_track_cache[cache_key]

        def _do_download():
            import time
            start_ts = time.time()
            max_wait_seconds = float(os.getenv("DEEMIX_WAIT_MAX_SECONDS", "12"))
            poll_interval = float(os.getenv("DEEMIX_POLL_INTERVAL", "0.1"))
            max_wait_loops = max(1, int(max_wait_seconds / poll_interval))

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

            # Wait for deemix to download the file (configurable, defaults to 12s)
            print(f"[Deemix] Waiting for download to complete...")
            file_found = False
            for attempt in range(max_wait_loops):
                time.sleep(poll_interval)
                
                # Check if listener captured the downloadPath (most reliable indicator)
                if listener.saved_path:
                    p = Path(listener.saved_path)
                    if p.exists() and p.stat().st_size > 0:
                        print(f"[Deemix] Download complete with captured path after {attempt * poll_interval:.1f}s")
                        file_found = True
                        break
                
                # Fallback: check if listener got the download-complete event
                if listener.completed and not listener.saved_path:
                    print(f"[Deemix] Download event received after {attempt * poll_interval:.1f}s (waiting for path capture)")
                    # Give a bit more time for updateQueue with downloadPath to arrive
                    time.sleep(0.3)
                    if listener.saved_path:
                        p = Path(listener.saved_path)
                        if p.exists() and p.stat().st_size > 0:
                            file_found = True
                            break
                    
            if not file_found:
                print(f"[Deemix] Did not capture downloadPath via events, will use file detection strategies")

            # Strategy 0: use listener's captured downloadPath directly (most reliable)
            new_files = set()
            if listener.saved_path:
                p = Path(listener.saved_path)
                if p.exists() and p.stat().st_size > 0:
                    new_files.add(p)
                    print(f"[Deemix] Using listener's captured downloadPath: {listener.saved_path}")
            
            # Strategy 1: time-based detection with a 3s buffer for clock skew
            if not new_files:
                def _new_files_in(path: Path):
                    result = set()
                    try:
                        for f in path.rglob("*"):
                            # Only include audio files that are >0 bytes
                            audio_exts = {".mp3", ".flac", ".m4a", ".ogg", ".opus"}
                            if f.is_file() and f.suffix.lower() in audio_exts and f.stat().st_size > 0 and f.stat().st_mtime >= (start_ts - 3):
                                result.add(f)
                    except Exception:
                        pass
                    return result

                # Fast path: most downloads are in the top-level cache directory.
                try:
                    audio_exts = {".mp3", ".flac", ".m4a", ".ogg", ".opus"}
                    top_level = {
                        f for f in cache_dir.glob("*")
                        if f.is_file() and f.suffix.lower() in audio_exts and f.stat().st_size > 0 and f.stat().st_mtime >= (start_ts - 3)
                    }
                except Exception:
                    top_level = set()
                new_files = top_level or _new_files_in(cache_dir)

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

            # Strategy 3: match by title/artist in filenames across the cache dir
            if not new_files and listener.completed:
                audio_exts = {".mp3", ".flac", ".m4a", ".ogg", ".opus"}
                for track_data in listener.completed:
                    t = track_data.get("title", "").lower()
                    a = track_data.get("artist", "").lower()
                    try:
                        for f in cache_dir.rglob("*"):
                            if f.is_file() and f.suffix.lower() in audio_exts and f.stat().st_size > 0:
                                name = f.name.lower()
                                if t and t in name:
                                    new_files.add(f)
                                    break
                                if a and a in name:
                                    new_files.add(f)
                                    break
                    except Exception:
                        pass

            if not new_files:
                raise RuntimeError(
                    f"Download ran but no matching file appeared in `{cache_dir}` or fallback dirs. "
                    "Check Railway logs for [Deemix] events to diagnose."
                )

            return (str(list(new_files)[0]), title, artist, listener.album_art_url)

        try:
            result = await asyncio.to_thread(_do_download)
            # Store in session cache so the same track isn't re-downloaded
            self.deezer_track_cache[cache_key] = result[0]
            return result
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Unexpected download error: {e}")

    async def play_via_lavalink_from_file(self, interaction: discord.Interaction, file_path: str, title: str, artist: str, album_art_url: str = None, progress_message: discord.Message | None = None):
        """
        Upload a local audio file to Discord CDN then play via Lavalink.
        Avoids discord.py native voice UDP (blocked on Railway).
        """
        print(f"[CDN] Starting CDN playback for: {title} - {artist}")
        print(f"[CDN] File path: {file_path}")
        
        # Upload file to Discord to get a streamable CDN URL
        # We delete immediately after getting the URL — Discord CDN serves the file
        # independently of the message existing.
        upload_msg = None
        try:
            f = discord.File(file_path, filename=Path(file_path).name)
            upload_msg = await interaction.channel.send(file=f, delete_after=0)
            cdn_url = upload_msg.attachments[0].url
            print(f"[CDN] File uploaded and scheduled for immediate deletion, CDN URL: {cdn_url[:80]}...")
        except Exception as e:
            print(f"[CDN] Upload failed: {e}")
            raise RuntimeError(f"Could not upload file to Discord CDN: {e}")

        await self.update_progress_message(progress_message, "Loading track", "Connecting player and preparing playback...")

        # Ensure Lavalink and voice
        await self.ensure_lavalink(interaction)
        try:
            player = await self.ensure_voice(interaction, notify=not bool(progress_message))
        except RuntimeError:
            raise

        # Wait for Discord CDN to be available and indexed by Lavalink
        print(f"[CDN] CDN URL: {cdn_url[:100]}...")
        cdn_index_wait = float(os.getenv("CDN_INDEX_WAIT_SECONDS", "1.5"))
        print(f"[CDN] Waiting {cdn_index_wait:.1f} seconds for Discord CDN to be indexed...")
        await asyncio.sleep(cdn_index_wait)

        # Try to load track via Lavalink REST API endpoint with retry logic
        track = None
        max_cdn_attempts = int(os.getenv("CDN_LOAD_MAX_ATTEMPTS", "4"))
        base_retry_wait = float(os.getenv("CDN_LOAD_RETRY_BASE_SECONDS", "0.35"))
        for attempt in range(1, max_cdn_attempts + 1):
            try:
                print(f"[CDN] Attempt {attempt}/{max_cdn_attempts}: Loading from CDN")
                
                # Get Lavalink node
                node = wavelink.Pool.get_node()
                if not node:
                    raise RuntimeError("No Lavalink node available")
              
                # Use Lavalink's REST endpoint to load tracks
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": node.password}
                    base_url = node.uri.replace('ws://', 'http://').replace('wss://', 'https://')
                    url = f"{base_url}/v4/loadtracks"
                    
                    async with session.get(url, headers=headers, params={"identifier": cdn_url}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            load_type = data.get('loadType', 'unknown')
                            data_obj = data.get('data')
                            
                            print(f"[CDN] Response: loadType={load_type}, dataObj={'present' if data_obj else 'null'}")
                            
                            # Handle different loadType responses
                            if load_type == "track" and data_obj:
                                print(f"[CDN] ✓ Track loaded on attempt {attempt}")
                                track = wavelink.Playable(data_obj)
                                break
                            elif load_type == "search" and isinstance(data_obj, list) and len(data_obj) > 0:
                                print(f"[CDN] ✓ Found via search on attempt {attempt}")
                                track = wavelink.Playable(data_obj[0])
                                break
                            elif load_type == "playlist" and isinstance(data_obj, dict) and 'tracks' in data_obj:
                                tracks = data_obj.get('tracks', [])
                                if len(tracks) > 0:
                                    print(f"[CDN] ✓ Playlist found on attempt {attempt}")
                                    track = wavelink.Playable(tracks[0])
                                    break
                        else:
                            print(f"[CDN] HTTP {resp.status} response")
                
                if not track and attempt < max_cdn_attempts:
                    wait_time = base_retry_wait * attempt
                    await asyncio.sleep(wait_time)
            except Exception as e:
                print(f"[CDN] Attempt {attempt} error: {type(e).__name__}: {e}")
                if attempt < max_cdn_attempts:
                    wait_time = base_retry_wait * attempt
                    await asyncio.sleep(wait_time)

        # Delete the upload message now that Lavalink has loaded (or failed to load) the track
        if upload_msg:
            try:
                await upload_msg.delete()
            except Exception:
                pass

        # Do not silently fallback to another source here; if loading the prepared
        # track fails, surface an explicit error to the user.
        if not track:
            raise RuntimeError("Could not load the prepared track for playback.")

        guild_id = interaction.guild.id

        # Check if something is already playing or queued in Lavalink
        current_track = getattr(player, "current", None)
        lavalink_queue = getattr(player, "queue", None)
        
        is_playing = current_track is not None
        has_queued = lavalink_queue and len(lavalink_queue) > 0
        
        if is_playing or has_queued:
            # Something is already playing - ADD TO QUEUE
            print(f"[Deezer] Currently playing/queued, adding to queue")
            try:
                player.queue.put(track)
                status = "Added to queue"
            except Exception as e:
                print(f"[Deezer] Queue.put failed: {e}, falling back to player.play()")
                await player.play(track)
                status = "Now playing"
        else:
            # Nothing playing - PLAY IMMEDIATELY
            print(f"[Deezer] Nothing playing, playing immediately")
            await self._apply_stored_filter_preset(guild_id, player)
            await player.play(track)
            status = "Now playing"
        
        # Store metadata for lyrics
        self.deezer_now_playing[guild_id] = (title, artist)

        if status == "Now playing":
            embed = discord.Embed(color=0x1DB954)
            embed.set_author(name="Now Playing")
        else:
            embed = discord.Embed(color=0x2b2d31)
            embed.set_author(name="Queued")

        embed.description = f"**{title}**\n{artist}"
        if album_art_url:
            embed.set_image(url=album_art_url)
        embed.set_footer(text=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if progress_message:
            try:
                await progress_message.edit(embed=embed)
            except Exception:
                await self.send_interaction(interaction, embed=embed)
        else:
            await self.send_interaction(interaction, embed=embed)
        await self.log_play_source(interaction, title, artist, "deezer_file", status)






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
        # Track last text channel per guild for idle disconnect messages
        if interaction.guild and interaction.channel:
            self._last_text_channel[interaction.guild.id] = interaction.channel.id

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

    def _build_progress_embed(self, step: str, detail: str | None = None) -> discord.Embed:
        embed = discord.Embed(color=0x5865F2)
        embed.set_author(name="Playback")
        embed.description = f"**{step}**" + (f"\n{detail}" if detail else "")
        embed.set_footer(text="CFrame Music")
        return embed

    def _build_progress_error_embed(self, message: str) -> discord.Embed:
        embed = discord.Embed(color=0xED4245)
        embed.set_author(name="Playback")
        embed.description = f"❌ {message}"
        embed.set_footer(text="CFrame Music")
        return embed

    async def start_progress_message(self, interaction: discord.Interaction, step: str, detail: str | None = None) -> discord.Message | None:
        try:
            msg = await interaction.followup.send(embed=self._build_progress_embed(step, detail), wait=True)
            if msg:
                self._progress_last_update_at[msg.id] = time.monotonic()
                self._progress_last_payload[msg.id] = (step, detail or "")
            return msg
        except Exception:
            return None

    async def update_progress_message(self, progress_message: discord.Message | None, step: str, detail: str | None = None):
        if not progress_message:
            return
        payload = (step, detail or "")
        msg_id = progress_message.id
        if self._progress_last_payload.get(msg_id) == payload:
            return

        now = time.monotonic()
        last_at = self._progress_last_update_at.get(msg_id, 0.0)
        if now - last_at < self._progress_min_interval:
            return

        try:
            await progress_message.edit(embed=self._build_progress_embed(step, detail))
            self._progress_last_update_at[msg_id] = now
            self._progress_last_payload[msg_id] = payload
        except Exception:
            pass

    async def fail_progress_message(self, progress_message: discord.Message | None, message: str):
        if not progress_message:
            return
        try:
            await progress_message.edit(embed=self._build_progress_error_embed(message))
        except Exception:
            pass

    async def log_play_source(self, interaction: discord.Interaction, title: str, artist: str, source: str, status_text: str):
        """Log playback source to stdout only (Railway logs)."""
        user_name = str(getattr(interaction, "user", "unknown"))
        print(f"[PlaySource] user={user_name} status={status_text} source={source} title={title} artist={artist}")

    async def ensure_lavalink(self, interaction: discord.Interaction):
        if await self.is_node_connected():
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.connect_node()
       
    def get_queue(self, guild_id: int):
        return self.queues.setdefault(guild_id, deque())

    def _build_filter_profile(self, preset: str) -> tuple[wavelink.Filters | None, str, str]:
        preset_val = (preset or "").strip().lower()

        if preset_val in {"reset", "off", "none"}:
            return None, "🎚️ Filters Reset", "All audio filters have been removed."

        filters = wavelink.Filters()

        if preset_val == "nightcore":
            filters.timescale.set(speed=1.2, pitch=1.25, rate=1.0)
            return filters, "🎚️ Nightcore Enabled", "Pitch: `1.25x`, Speed: `1.2x`"

        if preset_val == "slowed":
            filters.timescale.set(speed=0.85, pitch=0.85, rate=1.0)
            return filters, "🎚️ Slowed Enabled", "Pitch: `0.85x`, Speed: `0.85x`"

        if preset_val == "vaporwave":
            filters.timescale.set(speed=0.8, pitch=0.8, rate=1.0)
            return filters, "🎚️ Vaporwave Enabled", "Pitch: `0.80x`, Speed: `0.80x`"

        if preset_val == "deep":
            filters.timescale.set(speed=0.9, pitch=0.8, rate=1.0)
            return filters, "🎚️ Deep Voice Enabled", "Pitch: `0.80x`, Speed: `0.90x`"

        if preset_val == "chipmunk":
            filters.timescale.set(speed=1.15, pitch=1.35, rate=1.0)
            return filters, "🎚️ Chipmunk Enabled", "Pitch: `1.35x`, Speed: `1.15x`"

        if preset_val == "bassboost":
            bands = [
                {"band": 0, "gain": 0.30},
                {"band": 1, "gain": 0.25},
                {"band": 2, "gain": 0.20},
                {"band": 3, "gain": 0.15},
            ]
            filters.equalizer.set(bands=bands)
            return filters, "🎚️ Bassboost Enabled", "Low frequencies boosted."

        if preset_val == "distortion":
            filters.distortion.set(
                sin_scale=0.9,
                cos_scale=0.9,
                tan_scale=0.35,
                offset=0.0,
                scale=1.0,
            )
            return filters, "🎚️ Distortion Enabled", "Added a stronger distorted effect."

        if preset_val == "robot":
            filters.distortion.set(
                sin_scale=0.75,
                cos_scale=0.75,
                tan_scale=0.25,
                offset=0.0,
                scale=1.0,
            )
            filters.channel_mix.set(
                left_to_left=0.70,
                left_to_right=0.30,
                right_to_left=0.30,
                right_to_right=0.70,
            )
            return filters, "🎚️ Robot Enabled", "Distortion and channel mixing applied."

        if preset_val == "telephone":
            filters.equalizer.set(
                bands=[
                    {"band": 0, "gain": -0.25},
                    {"band": 1, "gain": -0.20},
                    {"band": 2, "gain": -0.10},
                    {"band": 3, "gain": 0.05},
                    {"band": 4, "gain": 0.15},
                    {"band": 5, "gain": 0.20},
                    {"band": 6, "gain": 0.15},
                    {"band": 7, "gain": 0.05},
                    {"band": 8, "gain": -0.05},
                    {"band": 9, "gain": -0.10},
                    {"band": 10, "gain": -0.15},
                    {"band": 11, "gain": -0.20},
                    {"band": 12, "gain": -0.25},
                    {"band": 13, "gain": -0.25},
                    {"band": 14, "gain": -0.25},
                ]
            )
            filters.low_pass.set(smoothing=8.0)
            return filters, "🎚️ Telephone Enabled", "Narrow-band voice effect applied."

        if preset_val == "mono":
            filters.channel_mix.set(
                left_to_left=0.50,
                left_to_right=0.50,
                right_to_left=0.50,
                right_to_right=0.50,
            )
            return filters, "🎚️ Mono Enabled", "Both channels are blended together."

        if preset_val == "wide":
            filters.channel_mix.set(
                left_to_left=0.85,
                left_to_right=0.15,
                right_to_left=0.15,
                right_to_right=0.85,
            )
            return filters, "🎚️ Wide Stereo Enabled", "Stereo crossfeed widened."

        if preset_val == "8d":
            filters.rotation.set(rotation_hz=0.2)
            return filters, "🎚️ 8D Audio Enabled", "Sound panning around stereo channels at `0.2 Hz`."

        if preset_val == "karaoke":
            filters.karaoke.set(level=1.0, mono_level=1.0, filter_band=220.0, filter_width=100.0)
            return filters, "🎚️ Karaoke Filter Enabled", "Vocal frequency range attenuated."

        if preset_val == "tremolo":
            filters.tremolo.set(frequency=4.0, depth=0.6)
            return filters, "🎚️ Tremolo Enabled", "Volume oscillation: `4 Hz`, Depth: `0.6`."

        if preset_val == "vibrato":
            filters.vibrato.set(frequency=4.0, depth=0.6)
            return filters, "🎚️ Vibrato Enabled", "Pitch oscillation: `4 Hz`, Depth: `0.6`."

        if preset_val == "lowpass":
            filters.low_pass.set(smoothing=20.0)
            return filters, "🎚️ LowPass Enabled", "Higher frequencies suppressed (smoothing: `20`)."

        return None, "❌ Unknown Filter", "That preset is not supported."

    async def _apply_stored_filter_preset(self, guild_id: int, player: wavelink.Player):
        preset = self.active_filter_presets.get(guild_id)
        if not preset:
            return

        filters, _, _ = self._build_filter_profile(preset)
        if filters is None:
            await player.set_filters()
            return

        await player.set_filters(filters)

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
        if not player or not player.channel:
            return

        st = self.voice_status.get(guild.id)
        if not st or not st.get("voice_status_enabled"):
            return

        channel = player.channel
        suffix = st.get("voice_status_suffix") or "🎵 {title}"
        # Discord channel name limit is 100 characters.
        new_name = f"{st.get('original_name', channel.name)} | {suffix.replace('{title}', track_title)}"
        if len(new_name) > 100:
            new_name = new_name[:97] + "..."

        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except Exception as e:
            print(f"[Voice Status] Failed to update name: {e}")

    async def _restore_voice_status(self, guild: discord.Guild):
        st = self.voice_status.get(guild.id)
        if not st:
            return
        player = self.active_players.get(guild.id)
        if not player or not player.channel:
            return
        original = st.get("original_name")
        if not original:
            return
        try:
            if player.channel.name != original:
                await player.channel.edit(name=original)
        except Exception as e:
            print(f"[Voice Status] Failed to restore name: {e}")

    async def queue_worker(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                # Clean up disconnected players from active_players dict
                for guild_id in list(self.active_players.keys()):
                    guild = self.bot.get_guild(guild_id)
                    if not guild or not guild.voice_client:
                        self.active_players.pop(guild_id, None)
                        self.last_playing_track.pop(guild_id, None)
                        continue
                    
                    player = self.active_players[guild_id]
                    if not player or not player.connected:
                        self.active_players.pop(guild_id, None)
                        self.last_playing_track.pop(guild_id, None)
                
                # Update voice channel name when track changes
                for guild_id, player in list(self.active_players.items()):
                    try:
                        if player and player.connected and player.playing and player.current:
                            current_track_id = getattr(player.current, 'identifier', None) or getattr(player.current, 'uri', None)
                            last_track_id = self.last_playing_track.get(guild_id)
                            
                            # Track changed, update voice channel name
                            if current_track_id and current_track_id != last_track_id:
                                self.last_playing_track[guild_id] = current_track_id
                                track_title = getattr(player.current, 'title', 'Unknown')
                                
                                # Restore original name first, then apply new suffix
                                await self._restore_voice_status(player.guild)
                                await self._update_voice_status(player, track_title)
                    except Exception as e:
                        print(f"[Queue] Error updating voice status for guild {guild_id}: {e}")
                
                # Auto-play next queued track from Wavelink queue if nothing is playing
                for guild_id, player in list(self.active_players.items()):
                    try:
                        if player and player.connected and not player.playing:
                            # Check if player has queued tracks
                            if player.queue and len(player.queue) > 0:
                                next_track = player.queue.get()
                                if next_track:
                                    print(f"[Queue] Auto-playing next track: {next_track.title}")
                                    await self._apply_stored_filter_preset(guild_id, player)
                                    await player.play(next_track)
                                    self.idle_since.pop(guild_id, None)
                            else:
                                # Nothing playing and queue empty — track idle time
                                import time as _time
                                if guild_id not in self.idle_since:
                                    self.idle_since[guild_id] = _time.time()
                                elif _time.time() - self.idle_since[guild_id] > 300:  # 5 min idle
                                    guild = self.bot.get_guild(guild_id)
                                    print(f"[Queue] Auto-disconnecting guild {guild_id} after 5 min idle")
                                    await self._restore_voice_status(player.guild)
                                    await player.disconnect()
                                    self.active_players.pop(guild_id, None)
                                    self.idle_since.pop(guild_id, None)
                                    if guild:
                                        # Find the text channel the bot last interacted in
                                        ch_id = self._last_text_channel.get(guild_id)
                                        ch = guild.get_channel(ch_id) if ch_id else None
                                        if ch:
                                            embed = discord.Embed(description="Left the voice channel after 5 minutes of inactivity.", color=0x2b2d31)
                                            try:
                                                await ch.send(embed=embed)
                                            except Exception:
                                                pass
                        else:
                            self.idle_since.pop(guild_id, None)
                    except Exception as e:
                        print(f"[Queue] Error checking auto-play for guild {guild_id}: {e}")
                
                # Process queued tracks
                for guild_id, q in list(self.queues.items()):
                    guild = self.bot.get_guild(guild_id)
                    if not guild or not guild.voice_client:
                        continue
                    player = guild.voice_client
                    if not isinstance(player, wavelink.Player):
                        continue

                    # Skip if currently playing or paused
                    if player.playing or player.paused:
                        continue

                    if not q:
                        continue

                    next_item = q.popleft()
                    track = next_item.get("track")
                    if not track:
                        continue

                    await self._apply_stored_filter_preset(guild_id, player)
                    await player.play(track)

                    title = next_item.get("title") or getattr(track, "title", "Unknown")
                    artist = next_item.get("artist") or getattr(track, "author", "Unknown Artist")
                    
                    # If this was a Deezer track, update now-playing metadata for lyrics
                    if "artist" in next_item:
                        self.deezer_now_playing[guild_id] = (title, artist)
                    
                    await self._update_voice_status(player, title)
                    await send_log(self.bot, "COMMAND", f"Now playing (queue): `{title}`")

            except Exception:
                pass

            await asyncio.sleep(1)


    async def ensure_voice(self, interaction: discord.Interaction, notify: bool = True):
        if not interaction.user.voice or not interaction.user.voice.channel:
            if notify:
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
        guild_id = interaction.guild_id
        
        # Check if we have an active player for this guild already
        if guild_id in self.active_players:
            player = self.active_players[guild_id]
            if player and player.channel == channel:
                return player
            elif player:
                # Player exists but in wrong channel, move it
                try:
                    await player.move_to(channel)
                    return player
                except Exception:
                    # If move fails, disconnect and reconnect
                    del self.active_players[guild_id]
        
        # No active player for this guild, create one
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.channel != channel:
            # Connected to different channel, move it
            try:
                await voice_client.move_to(channel)
                player = voice_client
            except Exception:
                # Move failed, create fresh connection
                player = await channel.connect(cls=wavelink.Player, self_deaf=True)
        elif voice_client:
            # Already connected to correct channel
            player = voice_client
        else:
            # Not connected at all, create new connection
            player = await channel.connect(cls=wavelink.Player, self_deaf=True)
        
        # Store player reference to keep it alive
        self.active_players[guild_id] = player

        try:
            await self._apply_stored_filter_preset(guild_id, player)
        except Exception:
            pass

        return player

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
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ I'm currently not in a voice channel.", ephemeral=True)
            return
        await voice_client.disconnect()
        self.queues.pop(guild_id, None)
        self.active_players.pop(guild_id, None)
        
        embed = make_embed("👋 Disconnected", "Successfully cleared the queue and left the channel.")
        await interaction.response.send_message(embed=embed)

    async def process_play_track(self, interaction: discord.Interaction, track: wavelink.Playable, progress_message: discord.Message | None = None):
        try:
            player = await self.ensure_voice(interaction, notify=not bool(progress_message))
        except RuntimeError as e:
            if progress_message:
                await self.fail_progress_message(progress_message, str(e))
            else:
                await self.send_interaction(interaction, content=f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            if progress_message:
                await self.fail_progress_message(progress_message, f"Could not join voice channel: {e}")
            else:
                await self.send_interaction(interaction, content=f"❌ Could not join voice channel: `{e}`", ephemeral=True)
            return

        guild_id = interaction.guild.id
        q = self.get_queue(guild_id)

        # Determine if we should play immediately or queue
        # Play now if nothing is currently playing or paused
        should_play_now = not (player.playing or player.paused)

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
                        if progress_message:
                            await self.fail_progress_message(progress_message, "That track could not be played. Please try a different song.")
                        else:
                            await self.send_interaction(
                                interaction,
                                content="⚠️ That track could not be played. Please try a different song.",
                            )
                    else:
                        if progress_message:
                            await self.fail_progress_message(progress_message, "That track could not be played. Please try another query.")
                        else:
                            await self.send_interaction(
                                interaction,
                                content="⚠️ That track could not be played. Please try another query.",
                            )
                    return
        else:
            q.append(item)
            status_text = "Added to queue"

        artwork = getattr(track, "artwork", None)
        author = getattr(track, "author", "Unknown Artist")
        title_text = item.get("title") or "Unknown"
        uri = item.get("uri")

        if status_text == "Now playing":
            embed = discord.Embed(color=0x1DB954)
            embed.set_author(name="Now Playing")
        else:
            pos = len(self.get_queue(interaction.guild.id))
            embed = discord.Embed(color=0x2b2d31)
            embed.set_author(name=f"Queued  ·  #{pos}")

        embed.description = f"**{title_text}**\n{author}" + (f"\n[Open]({uri})" if uri else "")
        if artwork:
            embed.set_image(url=artwork)
        embed.set_footer(text=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        if progress_message:
            try:
                await progress_message.edit(embed=embed)
            except Exception:
                pass
        else:
            try:
                await self.send_interaction(interaction, embed=embed)
            except Exception as response_error:
                print(f"Failed to finish interaction response: {response_error}")
                try:
                    await interaction.followup.send(embed=embed)
                except Exception:
                    pass

        uri_lower = str(item.get("uri") or "").lower()
        if "youtube.com" in uri_lower or "youtu.be" in uri_lower:
            source = "youtube"
        else:
            source = "other"

        await self.log_play_source(interaction, item["title"], author, source, status_text)

    async def _play_downloaded_track_with_retries(
        self,
        interaction: discord.Interaction,
        deezer_query: str,
        progress_message: discord.Message | None = None,
        preferred_album_art: str | None = None,
        max_attempts: int = 2,
    ) -> None:
        """Try downloaded-track playback multiple times to tolerate late file availability."""
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                await self.update_progress_message(
                    progress_message,
                    "Fetching track",
                    f"Preparing your track (attempt {attempt}/{max_attempts})...",
                )

                file_path, title, artist, album_art = await self.download_deezer_track(deezer_query)
                final_album_art = preferred_album_art or album_art

                await self.update_progress_message(progress_message, "Loading track", "Loading track data...")
                await self.play_via_lavalink_from_file(
                    interaction,
                    file_path,
                    title,
                    artist,
                    final_album_art,
                    progress_message=progress_message,
                )
                return
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    await self.update_progress_message(
                        progress_message,
                        "Fetching track",
                        "Track is still processing, retrying...",
                    )
                    await asyncio.sleep(2 * attempt)

        if last_error:
            raise last_error

    @discord.app_commands.command(name="play", description="Play a song")
    @discord.app_commands.describe(query="Song name or URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if await blocked(interaction):
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        deezer_only_mode = os.getenv("DEEZER_ONLY_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}

        progress_message = await self.start_progress_message(interaction, "Fetching track", f"Query: `{query[:120]}`")

        # === Direct URL path ===
        if query.startswith(("http://", "https://", "spotify:")):
            # Deezer URL
            if is_deezer_query(query):
                try:
                    await self._play_downloaded_track_with_retries(
                        interaction,
                        query,
                        progress_message=progress_message,
                    )
                except Exception as e:
                    await self.fail_progress_message(progress_message, str(e))
                    if not progress_message:
                        await self.send_interaction(interaction, content=f"❌ {e}")
                return

            if deezer_only_mode:
                msg = "This bot is currently configured for Deezer-only playback. Please use a Deezer link or search query."
                await self.fail_progress_message(progress_message, msg)
                if not progress_message:
                    await self.send_interaction(interaction, content=f"❌ {msg}")
                return

            # Other URL (YouTube, Spotify, etc.)
            try:
                await self.update_progress_message(progress_message, "Loading track", "Fetching track data...")
                await self.ensure_lavalink(interaction)
                await self.ensure_voice(interaction, notify=not bool(progress_message))
                search_query = normalize_query_for_lavalink(query)
                results = await wavelink.Pool.fetch_tracks(search_query)
                track = results[0] if isinstance(results, list) and results else (results.tracks[0] if getattr(results, "tracks", None) else None)
                if not track:
                    await self.fail_progress_message(progress_message, "No track found for that URL.")
                    if not progress_message:
                        await self.send_interaction(interaction, content="❌ No track found for that URL.")
                    return
                await self.process_play_track(interaction, track, progress_message=progress_message)
            except Exception as e:
                await self.fail_progress_message(progress_message, str(e))
                if not progress_message:
                    await self.send_interaction(interaction, content=f"❌ {e}")
            return

        # === Search path: primary search, then fallback ===
        deezer_ok = False
        if DEEMIX_AVAILABLE and os.getenv("DEEZER_ARL_TOKEN", "").strip():
            try:
                await self.update_progress_message(progress_message, "Fetching track", "Searching for the best match...")
                tracks = await search_deezer_tracks(query)
                if tracks:
                    track_id = tracks[0]["id"]
                    album_art = tracks[0].get("album_art")
                    await self._play_downloaded_track_with_retries(
                        interaction,
                        str(track_id),
                        progress_message=progress_message,
                        preferred_album_art=album_art,
                    )
                    deezer_ok = True
            except Exception as e:
                print(f"[Play] Deezer attempt failed: {e}")

        if deezer_only_mode and not deezer_ok:
            msg = "Could not play that request from Deezer. Please try another query or Deezer link."
            await self.fail_progress_message(progress_message, msg)
            if not progress_message:
                await self.send_interaction(interaction, content=f"❌ {msg}")
            return

        if not deezer_ok:
            try:
                await self.update_progress_message(progress_message, "Fetching track", "Trying another lookup path...")
                await self.ensure_lavalink(interaction)
                await self.ensure_voice(interaction, notify=not bool(progress_message))
                results = await wavelink.Pool.fetch_tracks(f"ytsearch:{query}")
                track = results[0] if isinstance(results, list) and results else (results.tracks[0] if getattr(results, "tracks", None) else None)
                if not track:
                    await self.fail_progress_message(progress_message, "No results found.")
                    if not progress_message:
                        await self.send_interaction(interaction, content="❌ No results found.")
                    return
                await self.process_play_track(interaction, track, progress_message=progress_message)
            except Exception as e:
                await self.fail_progress_message(progress_message, str(e))
                if not progress_message:
                    await self.send_interaction(interaction, content=f"❌ {e}")

    @discord.app_commands.command(name="nowplaying", description="Show what's currently playing with a progress bar")
    async def nowplaying(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        guild_id = interaction.guild.id
        player = self.active_players.get(guild_id)
        if not player or not player.playing or not player.current:
            await interaction.response.send_message("❌ Nothing is playing right now.", ephemeral=True)
            return

        track = player.current
        title = getattr(track, "title", "Unknown")
        author = getattr(track, "author", "Unknown Artist")
        uri = getattr(track, "uri", None)
        artwork = getattr(track, "artwork", None)
        duration_ms = getattr(track, "length", 0) or 0
        position_ms = getattr(player, "position", 0) or 0

        def fmt_time(ms: int) -> str:
            s = int(ms / 1000)
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

        BAR_LEN = 20
        if duration_ms > 0:
            filled = int((position_ms / duration_ms) * BAR_LEN)
            bar = "█" * filled + "░" * (BAR_LEN - filled)
            time_str = f"`{fmt_time(position_ms)} {bar} {fmt_time(duration_ms)}`"
        else:
            time_str = "`Live stream`"

        dz = self.deezer_now_playing.get(guild_id)
        if dz and dz[0] == title:
            author = dz[1]

        vol = self.volumes.get(guild_id, 100)
        repeat = self.repeat_modes.get(guild_id, "off")

        embed = discord.Embed(color=0x1DB954)
        embed.set_author(name="Now Playing")
        embed.description = f"### [{title}]({uri})\n{author}" if uri else f"### {title}\n{author}"
        embed.description += f"\n\n{time_str}"
        embed.add_field(name="Volume", value=f"{vol}%", inline=True)
        embed.add_field(name="Repeat", value=repeat.capitalize(), inline=True)
        queue_len = len(player.queue) if player.queue else 0
        if queue_len:
            embed.add_field(name="Up Next", value=f"{queue_len} track{'s' if queue_len != 1 else ''}", inline=True)
        if artwork:
            embed.set_image(url=artwork)
        embed.set_footer(text=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="seek", description="Seek to a position in the current track")
    @discord.app_commands.describe(position="Position to seek to, e.g. 1:30 or 90")
    async def seek(self, interaction: discord.Interaction, position: str):
        if await blocked(interaction):
            return
        guild_id = interaction.guild.id
        player = self.active_players.get(guild_id)
        if not player or not player.playing:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return

        # Parse position: supports "1:30", "1:30:00", or plain seconds "90"
        try:
            parts = position.strip().split(":")
            if len(parts) == 1:
                seconds = int(parts[0])
            elif len(parts) == 2:
                seconds = int(parts[0]) * 60 + int(parts[1])
            else:
                seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            position_ms = seconds * 1000
        except ValueError:
            await interaction.response.send_message("❌ Invalid format. Use `1:30` or `90`.", ephemeral=True)
            return

        duration_ms = getattr(player.current, "length", 0) or 0
        if duration_ms and position_ms > duration_ms:
            await interaction.response.send_message("❌ Position is beyond the track length.", ephemeral=True)
            return

        await player.seek(position_ms)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        embed = discord.Embed(description=f"⏩ Seeked to **{ts}**", color=0x1DB954)
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="skip", description="Skip the current track")
    @discord.app_commands.describe(reason="Skip reason (optional)")
    async def skip(self, interaction: discord.Interaction, reason: str = None):

        if await blocked(interaction):
            return
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player or not player.playing:
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
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player or not player.playing:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        await player.pause()
        embed = make_embed("⏸️ Paused", "Playback has been suspended. Use `/resume` to continue.")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player or not player.paused:
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
        guild_id = interaction.guild.id
        player = self.active_players.get(guild_id)
        if not player or not player.channel:
            await interaction.response.send_message("❌ I'm not connected to a voice channel.", ephemeral=True)
            return

        # store original name
        st = self._get_voice_channel_and_store_original(interaction.guild, player.channel)
        st["voice_status_enabled"] = True
        if suffix:
            st["voice_status_suffix"] = suffix
        await self._restore_voice_status(interaction.guild)  # normalize before applying

        # if currently playing, update immediately
        try:
            if player.playing:
                title = getattr(getattr(player, "current", None), "title", None) or "Unknown"
                await self._update_voice_status(player, title)
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

        guild_id = interaction.guild.id
        player = self.active_players.get(guild_id)
        if not player:
            await interaction.response.send_message("❌ Bot is not in a voice channel.", ephemeral=True)
            return

        current_track = getattr(player, "current", None)
        lavalink_queue = list(getattr(player, "queue", None) or [])
        custom_queue = list(self.get_queue(guild_id))

        all_queued = lavalink_queue + [i.get("track") for i in custom_queue if i.get("track")]
        all_titles = (
            [getattr(t, "title", "Unknown") for t in lavalink_queue] +
            [i.get("title") or getattr(i.get("track"), "title", "Unknown") for i in custom_queue]
        )

        if not current_track and not all_queued:
            await interaction.response.send_message("☕ The queue is empty.", ephemeral=True)
            return

        PAGE_SIZE = 10
        pages: list[discord.Embed] = []
        total = len(all_titles)

        def fmt_time(ms: int) -> str:
            if not ms:
                return ""
            s = int(ms / 1000)
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            return f" `{h}:{m:02d}:{s:02d}`" if h else f" `{m}:{s:02d}`"

        # Build pages
        chunks = [all_titles[i:i + PAGE_SIZE] for i in range(0, max(total, 1), PAGE_SIZE)] or [[]]
        for page_num, chunk in enumerate(chunks):
            embed = discord.Embed(color=0x2b2d31)
            embed.set_author(name=f"Queue  ·  {total} track{'s' if total != 1 else ''}")
            lines = []
            if page_num == 0 and current_track:
                ct = getattr(current_track, "title", "Unknown")
                dur = fmt_time(getattr(current_track, "length", 0))
                lines.append(f"**▶  {ct}**{dur}")
                lines.append("")
            for i, title in enumerate(chunk):
                num = page_num * PAGE_SIZE + i + 1
                t = all_titles[page_num * PAGE_SIZE + i] if page_num * PAGE_SIZE + i < len(all_titles) else title
                track_obj = all_queued[page_num * PAGE_SIZE + i] if page_num * PAGE_SIZE + i < len(all_queued) else None
                dur = fmt_time(getattr(track_obj, "length", 0)) if track_obj else ""
                lines.append(f"`{num}.` {title}{dur}")
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Page {page_num + 1}/{len(chunks)}")
            pages.append(embed)

        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0])
            return

        class QueuePaginator(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.page = 0

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    await btn_interaction.response.defer()
                    return
                self.page = (self.page - 1) % len(pages)
                await btn_interaction.response.edit_message(embed=pages[self.page], view=self)

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    await btn_interaction.response.defer()
                    return
                self.page = (self.page + 1) % len(pages)
                await btn_interaction.response.edit_message(embed=pages[self.page], view=self)

        await interaction.response.send_message(embed=pages[0], view=QueuePaginator())

    @discord.app_commands.command(name="deemix", description="Play a track from a direct link or search query")
    @discord.app_commands.describe(query="Track link or search query")
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
            await self.send_interaction(interaction, content=f"❌ Could not prepare playback: `{e}`", ephemeral=True)
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
                await self.send_interaction(interaction, content="❌ No track found for that query.")
                return
        except Exception as error:
            message = str(error).lower()
            if "master key" in message or "deezer" in message:
                await self.send_interaction(
                    interaction,
                    content="❌ This playback path is not available right now due to server configuration.",
                )
            else:
                await self.send_interaction(interaction, content=f"❌ Could not resolve playback: `{error}`")
            return

        await self.process_play_track(interaction, track)

    async def _fetch_lavalink_lyrics(self, player: wavelink.Player, track_title: str = None, query: str = None):
        if not await self.is_node_connected():
            print(f"[Lyrics] Node not connected")
            return None, None, None

        node = wavelink.Pool.get_node()
        if not node:
            print(f"[Lyrics] No node found")
            return None, None, None

        http_uri = node.uri.replace("ws://", "http://").replace("wss://", "https://")
        headers = {"Authorization": node.password}
        data = None

        if query:
            print(f"[Lyrics] Searching for lyrics with query: {query}")
            search_query = normalize_query_for_lavalink(query)
            print(f"[Lyrics] Normalized query: {search_query}")
            
            results = await wavelink.Pool.fetch_tracks(search_query)
            track = None
            if isinstance(results, list):
                track = results[0] if results else None
            else:
                track = results.tracks[0] if getattr(results, "tracks", None) else None
            
            if not track:
                print(f"[Lyrics] ❌ Search found no track")
                return None, query, None
            
            print(f"[Lyrics] Found track: {getattr(track, 'title', 'Unknown')}")
            encoded_track = getattr(track, "encoded", None)
            if not encoded_track:
                print(f"[Lyrics] ❌ Track has no encoded data")
                return None, query, None
            
            track_title = getattr(track, "title", query or "Unknown Song")
            url = f"{http_uri}/v4/lyrics/{encoded_track}"
            print(f"[Lyrics] Using lyrics endpoint: {url[:100]}...")
        else:
            current_track = getattr(player, "current", None)
            if not current_track:
                print(f"[Lyrics] No current track in player")
                return None, None, None
            track_title = getattr(current_track, "title", track_title or "Unknown Song")
            url = f"{http_uri}/v4/sessions/{node.session_id}/players/{player.guild.id}/lyrics"
            print(f"[Lyrics] Using player lyrics endpoint for: {track_title}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as resp:
                    print(f"[Lyrics] Response status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        print(f"[Lyrics] ✅ Got lyrics data")
                    elif resp.status == 204:
                        print(f"[Lyrics] No lyrics available (204)")
                        return None, track_title, None
                    else:
                        print(f"[Lyrics] Error response: {resp.status}")
        except Exception as e:
            print(f"[Lyrics] Request error: {e}")
            return None, track_title, None

        if not data or not data.get("text"):
            print(f"[Lyrics] ❌ No text in response")
            return None, track_title, None
        
        print(f"[Lyrics] ✅ Successfully fetched {len(data.get('text', ''))} characters of lyrics")
        return data, track_title, None

    def _split_lyrics_chunks(self, lyrics_text: str, max_length: int = 3500) -> list[str]:
        chunks = []
        current_chunk = ""

        for line in lyrics_text.splitlines():
            if len(current_chunk) + len(line) + 1 > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    async def _fetch_external_lyrics(self, title: str, artist: str) -> tuple[str | None, str | None, str | None]:
        search_title = (title or "").strip()
        search_artist = (artist or "").strip()
        if not search_title:
            return None, None, None

        async with aiohttp.ClientSession() as session:
            if search_artist:
                lyrics_ovh_url = f"https://api.lyrics.ovh/v1/{search_artist}/{search_title}"
                try:
                    async with session.get(lyrics_ovh_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            text = (data.get("lyrics") or "").strip()
                            if text:
                                return text, "Lyrics.ovh", "Lyrics.ovh"
                except Exception as e:
                    print(f"[Lyrics] Lyrics.ovh fallback error: {e}")

            search_terms = " ".join(part for part in [search_title, search_artist] if part).strip()
            if not search_terms:
                return None, None, None

            try:
                genius_search_url = "https://genius.com/api/search/multi"
                params = {"q": search_terms, "per_page": 5}
                headers = {"User-Agent": "Mozilla/5.0"}
                async with session.get(genius_search_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        return None, None, None
                    data = await resp.json()

                sections = data.get("response", {}).get("sections", [])
                song_url = None
                for section in sections:
                    if section.get("type") != "song":
                        continue
                    hits = section.get("hits", [])
                    if not hits:
                        continue
                    song = hits[0].get("result", {})
                    song_url = song.get("url")
                    if song_url:
                        break

                if not song_url:
                    return None, None, None

                async with session.get(song_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        return None, None, None
                    html = await resp.text()

                blocks = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', html, re.S)
                if not blocks:
                    return None, None, None

                lyric_parts: list[str] = []
                for block in blocks:
                    block = re.sub(r"<br\s*/?>", "\n", block, flags=re.I)
                    block = re.sub(r"<.*?>", "", block)
                    block = unescape(block).strip()
                    if block:
                        lyric_parts.append(block)

                text = "\n\n".join(lyric_parts).strip()
                if text:
                    return text, "Genius", "Genius"
            except Exception as e:
                print(f"[Lyrics] Genius fallback error: {e}")

        return None, None, None

    async def _send_lyrics_embeds(self, interaction: discord.Interaction, track_title: str, lyrics_text: str, source_name: str, provider: str):
        chunks = self._split_lyrics_chunks(lyrics_text)
        for idx, chunk in enumerate(chunks):
            page_title = f"🎤 Lyrics: {track_title}"
            if len(chunks) > 1:
                page_title += f" (Part {idx + 1}/{len(chunks)})"

            embed = discord.Embed(title=page_title, description=chunk, color=0x2b2d31)
            embed.set_footer(text="CFrame Music")

            if idx == 0:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.response.send_message(embed=embed)
            else:
                await interaction.followup.send(embed=embed)

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
            embed.set_footer(text="CFrame Music")
            
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
        discord.app_commands.Choice(name="Deep", value="deep"),
        discord.app_commands.Choice(name="Chipmunk", value="chipmunk"),
        discord.app_commands.Choice(name="Bassboost", value="bassboost"),
        discord.app_commands.Choice(name="Distortion", value="distortion"),
        discord.app_commands.Choice(name="Robot", value="robot"),
        discord.app_commands.Choice(name="Telephone", value="telephone"),
        discord.app_commands.Choice(name="Mono", value="mono"),
        discord.app_commands.Choice(name="Wide Stereo", value="wide"),
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

        preset_val = preset.value

        filters, title, description = self._build_filter_profile(preset_val)

        if filters is None:
            self.active_filter_presets.pop(interaction.guild_id, None)
            await player.set_filters()
            await interaction.followup.send(embed=make_embed(title, description))
            return

        self.active_filter_presets[interaction.guild_id] = preset_val
        await player.set_filters(filters)
        await interaction.followup.send(embed=make_embed(title, description))

    @discord.app_commands.command(name="shuffle", description="Shuffle the music queue")
    async def shuffle(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player or not player.queue or len(player.queue) == 0:
            await interaction.response.send_message("❌ The queue is empty.", ephemeral=True)
            return
        
        import random
        queue_list = list(player.queue)
        random.shuffle(queue_list)
        
        # Clear and rebuild queue
        player.queue.clear()
        for track in queue_list:
            player.queue.put(track)
        
        embed = make_embed("🔀 Queue Shuffled", f"Shuffled {len(queue_list)} tracks.")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="repeat", description="Set repeat mode")
    @discord.app_commands.describe(mode="repeat mode: off, one, or all")
    async def repeat(self, interaction: discord.Interaction, mode: str):
        if await blocked(interaction):
            return
        
        mode = mode.lower()
        if mode not in ["off", "one", "all"]:
            await interaction.response.send_message("❌ Mode must be `off`, `one`, or `all`.", ephemeral=True)
            return
        
        guild_id = interaction.guild_id
        self.repeat_modes[guild_id] = mode
        
        emoji = {"off": "⛔", "one": "🔂", "all": "🔁"}[mode]
        embed = make_embed(f"{emoji} Repeat Mode", f"Repeat: `{mode.upper()}`")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="volume", description="Set playback volume (0-100)")
    @discord.app_commands.describe(level="Volume level 0-100")
    async def volume(self, interaction: discord.Interaction, level: int):
        if await blocked(interaction):
            return
        
        if not 0 <= level <= 100:
            await interaction.response.send_message("❌ Volume must be between 0 and 100.", ephemeral=True)
            return
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player:
            await interaction.response.send_message("❌ Not connected to voice.", ephemeral=True)
            return
        
        await player.set_volume(level)
        self.volumes[guild_id] = level
        
        embed = make_embed("🔊 Volume Set", f"Volume: `{level}%`")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="remove", description="Remove a track from the queue")
    @discord.app_commands.describe(position="Position in queue (1-based)")
    async def remove(self, interaction: discord.Interaction, position: int):
        if await blocked(interaction):
            return
        
        guild_id = interaction.guild_id
        player = self.active_players.get(guild_id)
        
        if not player or not player.queue or len(player.queue) == 0:
            await interaction.response.send_message("❌ The queue is empty.", ephemeral=True)
            return
        
        if position < 1 or position > len(player.queue):
            await interaction.response.send_message(f"❌ Position must be between 1 and {len(player.queue)}.", ephemeral=True)
            return
        
        queue_list = list(player.queue)
        removed_track = queue_list.pop(position - 1)
        
        player.queue.clear()
        for track in queue_list:
            player.queue.put(track)
        
        embed = make_embed("🗑️ Track Removed", f"Removed: `{removed_track.title}`")
        await interaction.response.send_message(embed=embed)

    @discord.app_commands.command(name="lyrics", description="Show lyrics for the current song")
    async def lyrics(self, interaction: discord.Interaction):
        if await blocked(interaction):
            return
        
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass
        
        guild_id = interaction.guild_id
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player):
            await self.send_interaction(interaction, content="❌ The bot is not in a voice channel.", ephemeral=True)
            return

        title = None
        artist = None

        if guild_id in self.deezer_now_playing:
            title, artist = self.deezer_now_playing[guild_id]

        if not title:
            current_track = getattr(player, "current", None)
            if not current_track:
                await self.send_interaction(interaction, content="❌ No track playing.", ephemeral=True)
                return
            title = getattr(current_track, "title", "Unknown")
            artist = getattr(current_track, "author", "Unknown Artist")

        query = f"{title} {artist}".strip()
        data, track_title, _ = await self._fetch_lavalink_lyrics(player, query=query)
        if not data or not data.get("text"):
            fallback_text, source_name, provider = await self._fetch_external_lyrics(track_title or title, artist)
            if not fallback_text:
                await self.send_interaction(interaction, content=f"❌ No lyrics found for **{track_title or title}**.", ephemeral=True)
                return
            await self._send_lyrics_embeds(interaction, track_title or title, fallback_text, source_name or "External", provider or "External")
            return

        await self._send_lyrics_embeds(
            interaction,
            track_title or title,
            data["text"],
            data.get("sourceName", "Unknown Source"),
            data.get("provider", "LavaLyrics"),
        )

async def setup(bot):
    await bot.add_cog(Music(bot))

