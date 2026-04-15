"""
yt-dlp wrapper — enhanced for maximum compatibility.
Supports YouTube (including playlists), SoundCloud, Twitch, Vimeo, Bandcamp, and 1000+ sites.
Geo-bypass enabled. Lazy playlist loading for speed.
"""
import asyncio
import os
from pathlib import Path

import discord
import yt_dlp

# ── yt-dlp options ────────────────────────────────────────────────────────────
YDL_OPTIONS = {
    "format": "bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "age_limit": 99,
    "socket_timeout": 15,
    "retries": 3,
    "fragment_retries": 3,
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "android", "ios"],
        }
    },
}

# Flat playlist options — extract entries quickly (no audio URL yet)
YDL_PLAYLIST_FLAT = {
    **YDL_OPTIONS,
    "noplaylist": False,
    "extract_flat": "in_playlist",  # Fast: only get metadata, not audio URLs
}

# FFmpeg streaming options
FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
        "-analyzeduration 0 -loglevel warning"
    ),
    "options": "-vn -ar 48000 -ac 2",
}


# ── Song model ────────────────────────────────────────────────────────────────
class Song:
    """Represents a single playable track with metadata."""

    def __init__(self, data: dict, requester: discord.Member):
        self.requester   = requester
        self.title       = data.get("title") or "Unknown Title"
        self.url         = data.get("url")           # Direct audio stream URL (None = unresolved)
        self.webpage_url = data.get("webpage_url") or data.get("original_url") or ""
        self.thumbnail   = data.get("thumbnail", "")
        self.duration    = data.get("duration", 0)
        self.uploader    = data.get("uploader") or data.get("channel") or "Unknown"
        self.extractor   = data.get("extractor", "youtube")

    @property
    def is_resolved(self) -> bool:
        """True if this song has a usable audio stream URL."""
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
            raise ValueError(f"Song '{self.title}' has no audio URL — call resolve() first.")
        raw = discord.FFmpegPCMAudio(self.url, **FFMPEG_OPTIONS)
        return discord.PCMVolumeTransformer(raw, volume=volume)

    async def resolve(self) -> bool:
        """
        Fetch the direct audio stream URL for this song.
        Used for playlist entries that were loaded lazily.
        Returns True on success.
        """
        if self.is_resolved:
            return True
        data = await _fetch_single(self.webpage_url)
        if data:
            self.url         = data.get("url")
            self.thumbnail   = data.get("thumbnail") or self.thumbnail
            self.duration    = data.get("duration")  or self.duration
            self.uploader    = data.get("uploader")  or self.uploader
            return bool(self.url)
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────
def _is_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www."))


async def _run(fn) -> dict | None:
    """Run a blocking yt-dlp call in a thread executor."""
    try:
        return await asyncio.get_event_loop().run_in_executor(None, fn)
    except yt_dlp.utils.DownloadError as e:
        print(f"[yt-dlp] DownloadError: {e}")
        return None
    except Exception as e:
        print(f"[yt-dlp] Error: {e}")
        return None


async def _fetch_single(query: str) -> dict | None:
    """Fetch full yt-dlp info for a single track (URL or search query)."""
    def _go():
        search = query if _is_url(query) else f"ytsearch:{query}"
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            data = ydl.extract_info(search, download=False)
        if not data:
            return None
        if "entries" in data:
            entries = [e for e in data["entries"] if e]
            return entries[0] if entries else None
        return data

    return await _run(_go)


# ── Public API ────────────────────────────────────────────────────────────────
async def search_youtube(query: str) -> dict | None:
    """Resolve a URL or search query to a single track's full metadata."""
    return await _fetch_single(query)


async def extract_playlist(url: str, requester: discord.Member) -> list[Song]:
    """
    Load a playlist URL.
    - First entry: fully resolved (has audio URL, ready to play immediately)
    - Remaining entries: lazily loaded (url=None, resolved just before playing)
    Capped at 50 songs.
    """
    MAX = 50

    # Step 1: Fast flat extraction — just get titles + webpage URLs
    def _flat():
        with yt_dlp.YoutubeDL(YDL_PLAYLIST_FLAT) as ydl:
            data = ydl.extract_info(url, download=False)
        if not data:
            return []
        if "entries" in data:
            return [e for e in list(data["entries"])[:MAX] if e]
        return [data]

    raw_entries = await _run(_flat) or []
    if not raw_entries:
        return []

    songs: list[Song] = []

    for i, entry in enumerate(raw_entries):
        # Build a minimal Song — url=None means it needs lazy resolve
        song_data = {
            "title":       entry.get("title") or "Unknown",
            "url":         None,   # Will be resolved before playback
            "webpage_url": entry.get("url") or entry.get("webpage_url") or "",
            "thumbnail":   entry.get("thumbnail", ""),
            "duration":    entry.get("duration", 0),
            "uploader":    entry.get("uploader") or entry.get("channel") or "Unknown",
            "extractor":   entry.get("ie_key") or "youtube",
        }
        song = Song(song_data, requester)

        # Fully resolve the first entry so it can be played immediately
        if i == 0:
            resolved = await _fetch_single(song.webpage_url)
            if resolved:
                song.url       = resolved.get("url")
                song.thumbnail = resolved.get("thumbnail") or song.thumbnail
                song.duration  = resolved.get("duration")  or song.duration
                song.uploader  = resolved.get("uploader")  or song.uploader

        songs.append(song)

    return songs
