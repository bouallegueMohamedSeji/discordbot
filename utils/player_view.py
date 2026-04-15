"""
Interactive button controls for the Now Playing embed.
Provides a full player panel: Prev, Pause/Resume, Skip, Loop, Shuffle, Vol±, Stop.
"""
from __future__ import annotations

import discord

from utils import embeds as embed_builders
from utils.queue_manager import LoopMode, get_state


class PlayerView(discord.ui.View):
    """
    A persistent (no timeout) interactive view attached to the Now Playing message.
    Each guild gets its own PlayerView instance keyed by guild_id.
    """

    def __init__(self, cog, guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self._sync_states()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _vc(self) -> discord.VoiceClient | None:
        guild = self.cog.bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None

    def _sync_states(self):
        """Reflect current playback state onto button appearance."""
        state = get_state(self.guild_id)
        vc = self._vc()

        # ── Pause/Resume ──
        if vc and vc.is_paused():
            self.pause_resume.emoji  = discord.PartialEmoji.from_str("▶️")
            self.pause_resume.style  = discord.ButtonStyle.success
            self.pause_resume.label  = "Resume"
        else:
            self.pause_resume.emoji  = discord.PartialEmoji.from_str("⏸️")
            self.pause_resume.style  = discord.ButtonStyle.primary
            self.pause_resume.label  = "Pause"

        # ── Loop ──
        if state.loop_mode == LoopMode.SONG:
            self.loop_toggle.emoji  = discord.PartialEmoji.from_str("🔂")
            self.loop_toggle.style  = discord.ButtonStyle.success
            self.loop_toggle.label  = "Song"
        elif state.loop_mode == LoopMode.QUEUE:
            self.loop_toggle.emoji  = discord.PartialEmoji.from_str("🔁")
            self.loop_toggle.style  = discord.ButtonStyle.primary
            self.loop_toggle.label  = "Queue"
        else:
            self.loop_toggle.emoji  = discord.PartialEmoji.from_str("🔁")
            self.loop_toggle.style  = discord.ButtonStyle.secondary
            self.loop_toggle.label  = "Loop"

        # ── Previous: disable if no history ──
        self.go_previous.disabled = len(state.history) == 0

        # ── Volume labels show current level ──
        vol_pct = int(state.volume * 100)
        self.vol_down.label = f"🔉  {max(0, vol_pct - 10)}%"
        self.vol_up.label   = f"🔊  {min(200, vol_pct + 10)}%"

    async def _rebuild(self, interaction: discord.Interaction):
        """Rebuild embed + view and edit the Now Playing message in-place."""
        state = get_state(self.guild_id)
        new_view = PlayerView(self.cog, self.guild_id)

        if state.current:
            embed = embed_builders.now_playing_embed(
                state.current, state.volume, state.loop_mode
            )
        else:
            embed = embed_builders.error_embed("Queue ended — nothing left to play.")
            for child in new_view.children:
                child.disabled = True

        await interaction.response.edit_message(embed=embed, view=new_view)

    # ══════════════════════════════════════════════════════════════════════════
    # Row 0 — Playback Controls
    # ══════════════════════════════════════════════════════════════════════════

    @discord.ui.button(emoji="⏮️", label="Prev", style=discord.ButtonStyle.secondary, row=0)
    async def go_previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        if not state.history:
            await interaction.response.send_message(
                embed=embed_builders.error_embed("No previous song in history!"), ephemeral=True
            )
            return

        # Push current song back to the front of the queue
        if state.current:
            state.queue.appendleft(state.current)

        # Pop from history and play it
        prev = state.history.pop()
        state.current = prev

        vc = self._vc()
        if vc and vc.is_connected():
            if vc.is_playing() or vc.is_paused():
                vc.stop()

            # Resolve if URL expired or missing
            if not prev.is_resolved:
                ok = await prev.resolve()
                if not ok:
                    await interaction.response.send_message(
                        embed=embed_builders.error_embed(f"Couldn't reload **{prev.title}** — skipping."),
                        ephemeral=True,
                    )
                    return

            source = prev.create_source(state.volume)
            vc.play(source, after=lambda _: self.cog._after_song(self.guild_id))

        await self._rebuild(interaction)

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            await interaction.response.send_message(
                embed=embed_builders.error_embed("Not connected to a voice channel!"), ephemeral=True
            )
            return

        if vc.is_playing():
            vc.pause()
        elif vc.is_paused():
            vc.resume()

        await self._rebuild(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def go_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()  # Triggers _after_song → _play_next automatically

        await self._rebuild(interaction)

    @discord.ui.button(emoji="🔁", label="Loop", style=discord.ButtonStyle.secondary, row=0)
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        # Cycle: NONE → SONG → QUEUE → NONE
        if state.loop_mode == LoopMode.NONE:
            state.loop_mode = LoopMode.SONG
        elif state.loop_mode == LoopMode.SONG:
            state.loop_mode = LoopMode.QUEUE
        else:
            state.loop_mode = LoopMode.NONE

        await self._rebuild(interaction)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=0)
    async def do_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        if state.queue_length >= 2:
            state.shuffle()
        await self._rebuild(interaction)

    # ══════════════════════════════════════════════════════════════════════════
    # Row 1 — Volume & Stop
    # ══════════════════════════════════════════════════════════════════════════

    @discord.ui.button(label="🔉  40%", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.volume = round(max(0.0, state.volume - 0.10), 2)
        vc = self._vc()
        if vc and vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = state.volume
        await self._rebuild(interaction)

    @discord.ui.button(label="🔊  60%", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.volume = round(min(2.0, state.volume + 0.10), 2)
        vc = self._vc()
        if vc and vc.source and hasattr(vc.source, "volume"):
            vc.source.volume = state.volume
        await self._rebuild(interaction)

    @discord.ui.button(label="⏹️  Stop", style=discord.ButtonStyle.danger, row=1)
    async def do_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.clear()
        vc = self._vc()
        if vc:
            vc.stop()

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(description="⏹️  Stopped — queue cleared.", color=0xED4245),
            view=self,
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        """Catch any unhandled button errors and always respond to the interaction."""
        import traceback
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            msg = embed_builders.error_embed(f"Button error: `{error}`")
            if interaction.response.is_done():
                await interaction.followup.send(embed=msg, ephemeral=True)
            else:
                await interaction.response.send_message(embed=msg, ephemeral=True)
        except Exception:
            pass

