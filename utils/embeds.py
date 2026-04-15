"""Rich Discord embed builders for the music bot."""
from datetime import datetime, timezone

import discord

from utils.queue_manager import LoopMode

# ── Color Palette ─────────────────────────────────────────────────────────────
BLURPLE    = 0x5865F2   # Discord blurple — used for now-playing / queue
SUCCESS    = 0x57F287   # Green — confirmations
ERROR      = 0xED4245   # Red — errors
WARNING    = 0xFEE75C   # Yellow — warnings
ACCENT     = 0x9B59B6   # Purple — added-to-queue

LOOP_LABELS = {
    LoopMode.NONE:  "🔁 Off",
    LoopMode.SONG:  "🔂 Song",
    LoopMode.QUEUE: "🔁 Queue",
}

ITEMS_PER_PAGE = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Embeds ────────────────────────────────────────────────────────────────────

def now_playing_embed(song, volume: float, loop_mode: LoopMode) -> discord.Embed:
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"### [{song.title}]({song.webpage_url})",
        color=BLURPLE,
        timestamp=_now(),
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    embed.add_field(name="⏱ Duration",  value=f"`{song.duration_str}`",          inline=True)
    embed.add_field(name="🎙 Uploader",  value=song.uploader,                     inline=True)
    embed.add_field(name="🔊 Volume",    value=f"`{int(volume * 100)}%`",         inline=True)
    embed.add_field(name="🔁 Loop",      value=LOOP_LABELS.get(loop_mode, "Off"), inline=True)
    embed.set_footer(
        text=f"Requested by {song.requester.display_name}",
        icon_url=song.requester.display_avatar.url,
    )
    return embed


def added_to_queue_embed(song, position: int) -> discord.Embed:
    embed = discord.Embed(
        title="➕ Added to Queue",
        description=f"**[{song.title}]({song.webpage_url})**",
        color=ACCENT,
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    embed.add_field(name="⏱ Duration", value=f"`{song.duration_str}`", inline=True)
    embed.add_field(name="📋 Position", value=f"`#{position}`",         inline=True)
    embed.set_footer(
        text=f"Requested by {song.requester.display_name}",
        icon_url=song.requester.display_avatar.url,
    )
    return embed


def queue_embed(state, page: int = 1) -> discord.Embed:
    queue_list = list(state.queue)
    total_songs = len(queue_list)
    total_pages = max(1, (total_songs + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(1, min(page, total_pages))

    embed = discord.Embed(
        title="📋 Music Queue",
        color=BLURPLE,
        timestamp=_now(),
    )

    # Currently playing
    if state.current:
        embed.add_field(
            name="🎵 Now Playing",
            value=f"**[{state.current.title}]({state.current.webpage_url})** `{state.current.duration_str}`\n"
                  f"Requested by {state.current.requester.mention}",
            inline=False,
        )

    # Upcoming songs for this page
    if queue_list:
        start = (page - 1) * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        lines = []
        for i, song in enumerate(queue_list[start:end], start=start + 1):
            lines.append(
                f"`{i}.` **[{song.title}]({song.webpage_url})** `{song.duration_str}` — {song.requester.mention}"
            )
        embed.add_field(
            name=f"Up Next — {total_songs} song(s)",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(name="Up Next", value="Queue is empty — add songs with `/play`!", inline=False)

    loop_label = LOOP_LABELS.get(state.loop_mode, "Off")
    embed.set_footer(text=f"Page {page}/{total_pages}  •  Loop: {loop_label}")
    return embed


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅  {message}", color=SUCCESS)


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌  {message}", color=ERROR)


def searching_embed(query: str) -> discord.Embed:
    return discord.Embed(
        description=f"🔍  Searching for **{query}**...",
        color=WARNING,
    )
