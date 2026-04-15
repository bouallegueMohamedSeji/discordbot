"""
Audio backend — cookie-free, bot-detection-resistant.

Strategy (YouTube):
  1. Try Invidious API (open-source YT frontend, no bot checks)
     — rotates through several public instances for resilience
  2. Fall back to yt-dlp with tv_embedded client if Invidious fails

Strategy (everything else — SoundCloud, Bandcamp, Vimeo, etc.):
  - yt-dlp handles it directly (no YouTube bot-detection path is hit)
"""
import asyncio
import re
from typing import Optional

import aiohttp
import discord
import yt_dlp

# ── Invidious instances (public, no auth needed) ──────────────────────────────
# Ordered by reliability. Bot will try each in turn if one fails.
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacydev.net",
    "https://invidious.nerdvpn.de",
    "https://yt.artemislena.eu",
    "https://invidious.io.lol",
    "https://invidious.fdn.fr",
    "https://iv.melmac.space",
    "https://invidious.einfachzocken.eu",
]

# ── yt-dlp fallback options ───────────────────────────────────────────────────
YDL_OPTS_SINGLE = {
    "format": "bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "age_limit": 99,
    "socket_timeout": 20,
    "retries": 5,
    "fragment_retries": 5,
    "extractor_args": {
        "youtube": {
            # tv_embedded is most reliable without cookies
            "player_client": ["tv_embedded", "web_creator", "mweb"],
        }
    },
}

YDL_OPTS_PLAYLIST_FLAT = {
    **YDL_OPTS_SINGLE,
    "noplaylist": False,
    "extract_flat": "in_playlist",
}

# FFmpeg streaming options
FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-analyzeduration 0 -loglevel warning"
    ),
    "options": "-vn -ar 48000 -ac 2",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
_YT_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([A-Za-z0-9_-]{11})"
)
_YT_SEARCH_RE = re.compile(r"^(?!https?://)(.+)$")


def _is_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www."))


def _extract_video_id(url: str) -> Optional[str]:
    """Pull the 11-char video ID out of any YouTube URL."""
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _clean_youtube_url(url: str) -> str:
    """Strip list=/index=/si= params from a YouTube watch URL."""
    if not _is_youtube(url):
        return url
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("list", "index", "start_radio", "pp", "si"):
        params.pop(key, None)
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in params.items()})))


# ── Song model ────────────────────────────────────────────────────────────────
class Song:
    """Represents a single playable track."""

    def __init__(self, data: dict, requester: discord.Member):
        self.requester   = requester
        self.title       = data.get("title") or "Unknown Title"
        self.url         = data.get("url")           # Direct audio stream URL
        self.webpage_url = data.get("webpage_url") or data.get("original_url") or ""
        self.thumbnail   = data.get("thumbnail", "")
        self.duration    = data.get("duration", 0)
        self.uploader    = data.get("uploader") or data.get("channel") or "Unknown"
        self.extractor   = data.get("extractor", "youtube")

    @property
    def is_resolved(self) -> bool:
        return bool(self.url)

    @property
    def duration_str(self) -> str:
        if not self.duration:
            return "Live 🔴"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def create_source(self, volume: float = 0.5) -> discord.PCMVolumeTransformer:
        if not self.url:
            raise ValueError(f"Song '{self.title}' has no audio URL — resolve() first.")
        return discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(self.url, **FFMPEG_OPTIONS), volume=volume
        )

    async def resolve(self) -> bool:
        """Fetch the audio stream URL (for lazily-loaded playlist entries)."""
        if self.is_resolved:
            return True
        data = await _fetch_single(self.webpage_url)
        if data:
            self.url       = data.get("url")
            self.thumbnail = data.get("thumbnail") or self.thumbnail
            self.duration  = data.get("duration")  or self.duration
            self.uploader  = data.get("uploader")  or self.uploader
            return bool(self.url)
        return False


# ── Invidious API ─────────────────────────────────────────────────────────────
async def _invidious_fetch_by_id(video_id: str) -> Optional[dict]:
    """
    Query Invidious for a video's metadata + best audio stream URL.
    Rotates through all instances until one works.
    """
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"User-Agent": "Mozilla/5.0 (compatible; DiscordBot)"},
    ) as session:
        for base in INVIDIOUS_INSTANCES:
            url = f"{base}/api/v1/videos/{video_id}?fields=title,author,lengthSeconds,videoThumbnails,adaptiveFormats,formatStreams"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
                    audio_url = _best_audio_from_invidious(data, base)
                    if not audio_url:
                        continue
                    thumb = ""
                    thumbs = data.get("videoThumbnails") or []
                    # Prefer "high" quality thumbnail
                    for t in thumbs:
                        if t.get("quality") in ("high", "medium", "maxres"):
                            thumb = t.get("url", "")
                            break
                    if not thumb and thumbs:
                        thumb = thumbs[0].get("url", "")
                    return {
                        "title":       data.get("title") or "Unknown",
                        "url":         audio_url,
                        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail":   thumb,
                        "duration":    data.get("lengthSeconds") or 0,
                        "uploader":    data.get("author") or "Unknown",
                        "extractor":   "youtube",
                    }
            except Exception as e:
                print(f"[invidious] {base} failed: {e}")
                continue
    return None


def _best_audio_from_invidious(data: dict, instance_base: str) -> Optional[str]:
    """
    Pick the best audio-only stream from an Invidious video response.
    Prefer opus > webm > m4a/mp4 audio, then fall back to combined streams.
    Also rewrites relative URLs to absolute using the instance base.
    """
    def _fix_url(u: str) -> str:
        if u.startswith("/"):
            return instance_base + u
        return u

    formats = data.get("adaptiveFormats") or []
    audio_streams = [f for f in formats if f.get("type", "").startswith("audio/")]

    # Sort by bitrate descending (pick highest quality)
    audio_streams.sort(key=lambda f: int(f.get("bitrate") or 0), reverse=True)

    # Prefer opus/webm, then anything
    for preferred in ("opus", "webm", "mp4", "m4a"):
        for f in audio_streams:
            mime = f.get("type", "")
            if preferred in mime:
                url = f.get("url") or ""
                if url:
                    return _fix_url(url)

    # Fallback: any audio stream
    for f in audio_streams:
        url = f.get("url") or ""
        if url:
            return _fix_url(url)

    # Last resort: combined format streams
    for f in data.get("formatStreams") or []:
        url = f.get("url") or ""
        if url:
            return _fix_url(url)

    return None


async def _invidious_search(query: str) -> Optional[dict]:
    """
    Search YouTube via Invidious search API, return first result's full data.
    """
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
        headers={"User-Agent": "Mozilla/5.0 (compatible; DiscordBot)"},
    ) as session:
        for base in INVIDIOUS_INSTANCES:
            from urllib.parse import quote as _quote
            url = f"{base}/api/v1/search?q={_quote(query)}&type=video&sort_by=relevance"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    results = await resp.json(content_type=None)
                    if not results:
                        continue
                    video_id = results[0].get("videoId")
                    if not video_id:
                        continue
                    # Now fetch full data for that video ID
                    return await _invidious_fetch_by_id(video_id)
            except Exception as e:
                print(f"[invidious-search] {base} failed: {e}")
                continue
    return None


# ── yt-dlp fallback ───────────────────────────────────────────────────────────
async def _run_ydl(fn) -> Optional[dict]:
    """Run a blocking yt-dlp call in a thread executor."""
    try:
        return await asyncio.get_event_loop().run_in_executor(None, fn)
    except yt_dlp.utils.DownloadError as e:
        print(f"[yt-dlp] DownloadError: {e}")
        return None
    except Exception as e:
        print(f"[yt-dlp] Error: {type(e).__name__}: {e}")
        return None


async def _ytdlp_fetch_single(query: str) -> Optional[dict]:
    """yt-dlp fallback for non-YouTube or when Invidious fails."""
    def _go():
        target = query if _is_url(query) else f"ytsearch:{query}"
        with yt_dlp.YoutubeDL(YDL_OPTS_SINGLE) as ydl:
            data = ydl.extract_info(target, download=False)
        if not data:
            return None
        if "entries" in data:
            entries = [e for e in data["entries"] if e]
            return entries[0] if entries else None
        return data
    return await _run_ydl(_go)


# ── Core fetch logic ──────────────────────────────────────────────────────────
async def _fetch_single(query: str) -> Optional[dict]:
    """
    Universal track resolver:
    - YouTube URL  → Invidious by video ID → yt-dlp fallback
    - Search query → Invidious search      → yt-dlp fallback
    - Other URL    → yt-dlp directly
    """
    if _is_url(query):
        clean = _clean_youtube_url(query)

        if _is_youtube(clean):
            video_id = _extract_video_id(clean)
            if video_id:
                print(f"[invidious] Fetching ID: {video_id}")
                result = await _invidious_fetch_by_id(video_id)
                if result:
                    return result
                print(f"[invidious] All instances failed — trying yt-dlp fallback")

        # Non-YouTube URL or Invidious failed
        return await _ytdlp_fetch_single(clean)

    else:
        # Text search query
        print(f"[invidious] Searching: {query}")
        result = await _invidious_search(query)
        if result:
            return result
        print(f"[invidious] Search failed — trying yt-dlp fallback")
        return await _ytdlp_fetch_single(query)


# ── Public API ────────────────────────────────────────────────────────────────
async def search_youtube(query: str) -> Optional[dict]:
    """Resolve a URL or search query to a single track's full metadata."""
    return await _fetch_single(query)


async def extract_playlist(url: str, requester: discord.Member) -> list[Song]:
    """
    Load a YouTube playlist URL using yt-dlp's flat extraction
    (only metadata, no audio URLs — those are resolved lazily).
    First entry is fully resolved immediately so playback starts fast.
    Capped at 50 songs.
    """
    MAX = 50

    def _flat():
        with yt_dlp.YoutubeDL(YDL_OPTS_PLAYLIST_FLAT) as ydl:
            data = ydl.extract_info(url, download=False)
        if not data:
            return []
        if "entries" in data:
            return [e for e in list(data["entries"])[:MAX] if e]
        return [data]

    raw_entries = await _run_ydl(_flat) or []
    if not raw_entries:
        return []

    songs: list[Song] = []
    for i, entry in enumerate(raw_entries):
        song_data = {
            "title":       entry.get("title") or "Unknown",
            "url":         None,
            "webpage_url": entry.get("url") or entry.get("webpage_url") or "",
            "thumbnail":   entry.get("thumbnail", ""),
            "duration":    entry.get("duration", 0),
            "uploader":    entry.get("uploader") or entry.get("channel") or "Unknown",
            "extractor":   entry.get("ie_key") or "youtube",
        }
        song = Song(song_data, requester)

        # Resolve the first track immediately so it can start playing
        if i == 0:
            resolved = await _fetch_single(song.webpage_url)
            if resolved:
                song.url       = resolved.get("url")
                song.thumbnail = resolved.get("thumbnail") or song.thumbnail
                song.duration  = resolved.get("duration")  or song.duration
                song.uploader  = resolved.get("uploader")  or song.uploader

        songs.append(song)

    return songs
