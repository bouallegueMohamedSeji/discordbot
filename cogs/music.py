"""
Music Cog — all slash commands for playback, queue management,
loop modes, and volume control.
"""
import asyncio
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds
from utils.player_view import PlayerView
from utils.queue_manager import GuildMusicState, LoopMode, get_state, remove_state
from utils.ytdl import Song, extract_playlist, search_youtube

INACTIVITY_TIMEOUT = 300  # seconds (5 minutes)


def _is_playlist_url(query: str) -> bool:
    """
    Returns True ONLY for genuine playlist pages.
    Single YouTube videos that happen to have &list= in the URL (Mixes, autoplay)
    are treated as single tracks — the user wants just that video.
    """
    if not query.startswith(("http://", "https://")):
        return False

    # Single YouTube video — always play just that video, ignore any list= param
    if "youtube.com/watch?v=" in query or "youtu.be/" in query:
        return False

    # Genuine YouTube playlist page (URL contains /playlist)
    if "youtube.com/playlist" in query:
        return True

    # SoundCloud sets
    if "/sets/" in query:
        return True

    return False


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Maps guild_id -> inactivity disconnect Task
        self._inactivity_tasks: dict[int, asyncio.Task] = {}

    # ══════════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _ensure_voice(self, interaction: discord.Interaction) -> bool:
        """Return True if the user is in a voice channel, else send an error."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                embed=embeds.error_embed("You need to be in a voice channel first!"),
                ephemeral=True,
            )
            return False
        return True

    async def _get_voice_client(self, interaction: discord.Interaction) -> discord.VoiceClient:
        """Join the user's channel (or move there if already somewhere else)."""
        channel = interaction.user.voice.channel
        state = get_state(interaction.guild_id)
        existing = interaction.guild.voice_client

        if existing and existing.is_connected():
            # Already connected — move to user's channel if different
            if existing.channel != channel:
                await existing.move_to(channel)
            vc = existing
        else:
            # No connection or stale/disconnected — clean up and reconnect
            if existing:
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    pass
            vc = await channel.connect()

        state.voice_client = vc
        return vc

    def _cancel_inactivity(self, guild_id: int):
        """Cancel any pending auto-disconnect task."""
        task = self._inactivity_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    def _schedule_inactivity(self, guild_id: int):
        """Disconnect the bot after INACTIVITY_TIMEOUT seconds of silence."""
        self._cancel_inactivity(guild_id)

        async def _idle_disconnect():
            await asyncio.sleep(INACTIVITY_TIMEOUT)
            state = get_state(guild_id)
            vc = state.voice_client
            if vc and vc.is_connected() and not vc.is_playing():
                await vc.disconnect()
                remove_state(guild_id)

        task = asyncio.create_task(_idle_disconnect())
        self._inactivity_tasks[guild_id] = task

    async def _play_next(self, guild_id: int):
        """
        Called (thread-safely) when a song finishes.
        Advances the queue and plays the next track.
        Also updates the stored Now Playing message.
        """
        state = get_state(guild_id)
        next_song = state.next_song()

        if next_song is None:
            # Queue ended — disable all buttons on the NP message
            if state.np_message:
                try:
                    disabled_view = PlayerView(self, guild_id)
                    for child in disabled_view.children:
                        child.disabled = True
                    await state.np_message.edit(
                        embed=discord.Embed(
                            description="✅  Queue finished! Add more songs with `/play`.",
                            color=0x57F287,
                        ),
                        view=disabled_view,
                    )
                except Exception:
                    pass
            self._schedule_inactivity(guild_id)
            return

        vc = state.voice_client
        if vc and vc.is_connected():
            # Resolve the audio URL if this is a lazy playlist entry
            if not next_song.is_resolved:
                resolved = await next_song.resolve()
                if not resolved:
                    # Can't get audio URL — skip to the next song silently
                    print(f"[player] Skipping unresolvable track: {next_song.title}")
                    await self._play_next(guild_id)
                    return

            source = next_song.create_source(state.volume)
            vc.play(source, after=lambda _: self._after_song(guild_id))

            # Update the persistent Now Playing message
            if state.np_message:
                try:
                    await state.np_message.edit(
                        embed=embeds.now_playing_embed(next_song, state.volume, state.loop_mode),
                        view=PlayerView(self, guild_id),
                    )
                except Exception:
                    pass

    def _after_song(self, guild_id: int):
        """
        Called by discord.py in a non-async thread after a song ends.
        Schedules _play_next on the event loop.
        """
        coro = self._play_next(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    # ══════════════════════════════════════════════════════════════════════════
    # Playback Commands
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="play", description="▶️  Play a song — paste a YouTube URL or type a search query")
    @app_commands.describe(query="YouTube URL or search term (e.g. 'lofi hip hop')")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        if not await self._ensure_voice(interaction):
            return

        # Cancel any pending inactivity disconnect
        self._cancel_inactivity(interaction.guild_id)

        # Show searching feedback immediately
        await interaction.followup.send(embed=embeds.searching_embed(query))

        # ── Playlist detection ─────────────────────────────────────────────
        if _is_playlist_url(query):
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"💼  Detected a **playlist** — loading up to 50 tracks...",
                    color=0x5865F2,
                )
            )
            songs = await extract_playlist(query, interaction.user)
            if not songs:
                await interaction.followup.send(
                    embed=embeds.error_embed("Couldn't load the playlist. It may be private or unavailable.")
                )
                return

            vc = await self._get_voice_client(interaction)
            state = get_state(interaction.guild_id)
            added = 0

            for song in songs:
                if not (vc.is_playing() or vc.is_paused()) and state.current is None:
                    # Ensure the first song is resolved before playing
                    if not song.is_resolved:
                        ok = await song.resolve()
                        if not ok:
                            continue  # First song failed to resolve — try next
                    state.current = song
                    try:
                        source = song.create_source(state.volume)
                        vc.play(source, after=lambda _: self._after_song(interaction.guild_id))
                        np_embed = embeds.now_playing_embed(song, state.volume, state.loop_mode)
                        view = PlayerView(self, interaction.guild_id)
                        msg = await interaction.followup.send(embed=np_embed, view=view)
                        state.np_message = msg
                    except Exception as e:
                        print(f"[playlist] Failed to start first song: {e}")
                        state.current = None
                        continue
                else:
                    state.enqueue(song)
                added += 1

            await interaction.followup.send(
                embed=embeds.success_embed(
                    f"Queued **{added}** tracks from the playlist!  💼"
                )
            )
            return

        # ── Single track ──────────────────────────────────────────────────────
        data = await search_youtube(query)
        if not data:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    f"Couldn't find anything for: **{query}**\n\n"
                    f"-# YouTube may be rate-limiting this bot. Try again in a moment, "
                    f"or try a different search term."
                )
            )
            return

        vc = await self._get_voice_client(interaction)
        state = get_state(interaction.guild_id)
        song = Song(data, interaction.user)

        if vc.is_playing() or vc.is_paused():
            # Queue the song
            position = state.enqueue(song)
            await interaction.followup.send(embed=embeds.added_to_queue_embed(song, position))
        else:
            # Play immediately — attach the interactive button panel
            state.current = song
            try:
                source = song.create_source(state.volume)
                vc.play(source, after=lambda _: self._after_song(interaction.guild_id))
                np_embed = embeds.now_playing_embed(song, state.volume, state.loop_mode)
                view = PlayerView(self, interaction.guild_id)
                msg = await interaction.followup.send(embed=np_embed, view=view)
                state.np_message = msg
            except discord.ClientException as e:
                state.current = None
                await interaction.followup.send(
                    embed=embeds.error_embed(f"Voice connection error: `{e}`\nPlease try `/play` again.")
                )

    @app_commands.command(name="skip", description="⏭️  Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message(
                embed=embeds.error_embed("Nothing is playing right now!"), ephemeral=True
            )
            return

        state = get_state(interaction.guild_id)
        title = state.current.title if state.current else "Unknown"
        vc.stop()  # triggers _after_song → _play_next
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Skipped **{title}**")
        )

    @app_commands.command(name="pause", description="⏸️  Pause the current song")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            await interaction.response.send_message(
                embed=embeds.error_embed("Nothing is playing!"), ephemeral=True
            )
            return

        vc.pause()
        await interaction.response.send_message(embed=embeds.success_embed("Paused ⏸️"))

    @app_commands.command(name="resume", description="▶️  Resume the paused song")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_paused():
            await interaction.response.send_message(
                embed=embeds.error_embed("Nothing is paused!"), ephemeral=True
            )
            return

        vc.resume()
        await interaction.response.send_message(embed=embeds.success_embed("Resumed ▶️"))

    @app_commands.command(name="stop", description="⏹️  Stop playback and clear the entire queue")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(
                embed=embeds.error_embed("I'm not in a voice channel!"), ephemeral=True
            )
            return

        state = get_state(interaction.guild_id)
        state.clear()
        vc.stop()
        await interaction.response.send_message(
            embed=embeds.success_embed("Stopped playback and cleared the queue ⏹️")
        )

    @app_commands.command(name="leave", description="👋  Disconnect the bot from the voice channel")
    async def leave(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(
                embed=embeds.error_embed("I'm not in a voice channel!"), ephemeral=True
            )
            return

        self._cancel_inactivity(interaction.guild_id)
        state = get_state(interaction.guild_id)
        state.clear()
        await vc.disconnect()
        remove_state(interaction.guild_id)
        await interaction.response.send_message(embed=embeds.success_embed("Disconnected 👋"))

    # ══════════════════════════════════════════════════════════════════════════
    # Now Playing & Queue
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="nowplaying", description="🎵  Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        state = get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nothing is playing!"), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=embeds.now_playing_embed(state.current, state.volume, state.loop_mode)
        )

    @app_commands.command(name="queue", description="📋  Show the music queue")
    @app_commands.describe(page="Page number to view (default: 1)")
    async def queue(self, interaction: discord.Interaction, page: int = 1):
        state = get_state(interaction.guild_id)
        if not state.current and not state.queue:
            await interaction.response.send_message(
                embed=embeds.error_embed("The queue is empty! Add songs with `/play`."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=embeds.queue_embed(state, page))

    # ══════════════════════════════════════════════════════════════════════════
    # Queue Management
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="remove", description="🗑️  Remove a song from the queue by its position")
    @app_commands.describe(position="Position in the queue (starting from 1)")
    async def remove(self, interaction: discord.Interaction, position: int):
        state = get_state(interaction.guild_id)
        if position < 1 or position > state.queue_length:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Invalid position. The queue has **{state.queue_length}** song(s)."
                ),
                ephemeral=True,
            )
            return

        removed = state.remove_at(position)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Removed **{removed.title}** from position `#{position}`.")
        )

    @app_commands.command(name="move", description="↕️  Move a song to a different position in the queue")
    @app_commands.describe(from_pos="Current position", to_pos="New position")
    async def move(self, interaction: discord.Interaction, from_pos: int, to_pos: int):
        state = get_state(interaction.guild_id)
        length = state.queue_length
        if any(p < 1 or p > length for p in (from_pos, to_pos)):
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Positions must be between 1 and {length}."),
                ephemeral=True,
            )
            return

        song = state.move(from_pos, to_pos)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Moved **{song.title}** to position `#{to_pos}`.")
        )

    @app_commands.command(name="shuffle", description="🔀  Shuffle the upcoming queue randomly")
    async def shuffle(self, interaction: discord.Interaction):
        state = get_state(interaction.guild_id)
        if state.queue_length < 2:
            await interaction.response.send_message(
                embed=embeds.error_embed("Need at least 2 songs in the queue to shuffle!"),
                ephemeral=True,
            )
            return

        state.shuffle()
        await interaction.response.send_message(
            embed=embeds.success_embed(f"🔀 Shuffled **{state.queue_length}** songs!")
        )

    @app_commands.command(name="clear", description="🗑️  Clear all songs from the queue")
    async def clear(self, interaction: discord.Interaction):
        state = get_state(interaction.guild_id)
        count = state.queue_length
        state.queue.clear()
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Cleared **{count}** song(s) from the queue.")
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Audio Controls
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="volume", description="🔊  Set the playback volume (0–200%)")
    @app_commands.describe(level="Volume level from 0 to 200")
    async def volume(self, interaction: discord.Interaction, level: int):
        if not 0 <= level <= 200:
            await interaction.response.send_message(
                embed=embeds.error_embed("Volume must be between **0** and **200**."),
                ephemeral=True,
            )
            return

        state = get_state(interaction.guild_id)
        state.volume = level / 100

        # Apply immediately if something is playing
        vc = interaction.guild.voice_client
        if vc and vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = state.volume

        bar_filled = int(level / 10)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Volume set to **{level}%**\n`{bar}`")
        )

    @app_commands.command(name="loop", description="🔁  Cycle loop mode: Off → Song → Queue → Off")
    async def loop(self, interaction: discord.Interaction):
        state = get_state(interaction.guild_id)

        if state.loop_mode == LoopMode.NONE:
            state.loop_mode = LoopMode.SONG
            msg = "🔂 Now looping the **current song**."
        elif state.loop_mode == LoopMode.SONG:
            state.loop_mode = LoopMode.QUEUE
            msg = "🔁 Now looping the **entire queue**."
        else:
            state.loop_mode = LoopMode.NONE
            msg = "Loop is now **off**."

        await interaction.response.send_message(embed=embeds.success_embed(msg))

    # ══════════════════════════════════════════════════════════════════════════
    # Error handler
    # ══════════════════════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Auto-disconnect if the bot is left alone in a voice channel."""
        if member.bot:
            return

        vc = member.guild.voice_client
        if not vc:
            return

        # If the channel now has only the bot, start the inactivity timer
        non_bot_members = [m for m in vc.channel.members if not m.bot]
        if len(non_bot_members) == 0:
            self._schedule_inactivity(member.guild.id)
        else:
            self._cancel_inactivity(member.guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))

