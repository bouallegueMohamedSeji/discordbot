"""Per-guild music state and queue management."""
import asyncio
import random
from collections import deque
from enum import IntEnum

import discord


class LoopMode(IntEnum):
    NONE = 0
    SONG = 1
    QUEUE = 2


class GuildMusicState:
    """Holds all playback state for a single guild."""

    def __init__(self):
        self.queue: deque = deque()
        self.history: deque = deque(maxlen=20)  # Last 20 songs
        self.current = None          # Current Song object
        self.loop_mode = LoopMode.NONE
        self.volume: float = 0.5    # 50% default
        self.voice_client: discord.VoiceClient | None = None
        self.np_message = None       # Reference to the Now Playing Discord message

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self.voice_client is not None and self.voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.voice_client is not None and self.voice_client.is_paused()

    @property
    def queue_length(self) -> int:
        return len(self.queue)

    # ── Queue Operations ─────────────────────────────────────────────────────

    def enqueue(self, song) -> int:
        """Add a song to the end of the queue. Returns its 1-based position."""
        self.queue.append(song)
        return len(self.queue)

    def next_song(self):
        """
        Advance to the next song according to the current loop mode.
        Returns the next Song, or None if the queue is empty.
        """
        if self.loop_mode == LoopMode.SONG and self.current:
            # Keep playing the same song (don't add to history)
            return self.current

        # Push current song to history before advancing
        if self.current:
            self.history.append(self.current)

        if self.loop_mode == LoopMode.QUEUE and self.current:
            # Re-add the current song to the end before advancing
            self.queue.append(self.history[-1])

        if self.queue:
            self.current = self.queue.popleft()
            return self.current

        self.current = None
        return None

    def shuffle(self):
        """Randomly shuffle the upcoming queue."""
        songs = list(self.queue)
        random.shuffle(songs)
        self.queue = deque(songs)

    def remove_at(self, position: int):
        """Remove a song from the queue at the given 1-based position. Returns the removed song."""
        songs = list(self.queue)
        song = songs.pop(position - 1)
        self.queue = deque(songs)
        return song

    def move(self, from_pos: int, to_pos: int):
        """Move a song from one position to another (1-based). Returns the moved song."""
        songs = list(self.queue)
        song = songs.pop(from_pos - 1)
        songs.insert(to_pos - 1, song)
        self.queue = deque(songs)
        return song

    def clear(self):
        """Clear the queue, history, and reset current song."""
        self.queue.clear()
        self.history.clear()
        self.current = None
        self.np_message = None


# ── Module-Level Guild State Registry ────────────────────────────────────────

_states: dict[int, GuildMusicState] = {}


def get_state(guild_id: int) -> GuildMusicState:
    """Get or create the music state for a guild."""
    if guild_id not in _states:
        _states[guild_id] = GuildMusicState()
    return _states[guild_id]


def remove_state(guild_id: int):
    """Delete the music state for a guild (call on disconnect)."""
    _states.pop(guild_id, None)
