"""
Premium Discord Music Bot — Entry Point
"""
import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
APPLICATION_ID = 1493754083440459858  # Your app ID

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN not found!\n"
        "Copy .env.example to .env and paste your bot token."
    )

# ── Intents ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.voice_states = True  # Needed to track users in voice channels

# ── Bot ───────────────────────────────────────────────────────────────────────
bot = commands.Bot(
    command_prefix="!",  # Fallback prefix (unused — we use slash commands)
    intents=intents,
    application_id=APPLICATION_ID,
    help_command=None,
)


@bot.event
async def on_ready():
    print(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"📡  Connected to {len(bot.guilds)} guild(s)")
    print("🔄  Syncing slash commands...")

    await bot.tree.sync()
    print("✅  Slash commands synced!")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="your music 🎵 — /play",
        )
    )


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Sync commands when joining a new server."""
    await bot.tree.sync(guild=guild)
    print(f"✅  Joined guild: {guild.name} ({guild.id})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """Global slash command error handler."""
    original = getattr(error, "original", error)

    # Build a useful message — always show type name even if message is empty
    err_type = type(original).__name__
    err_msg  = str(original).strip()
    detail   = f"{err_type}: {err_msg}" if err_msg else err_type

    # Friendly messages for common Discord errors
    if isinstance(original, discord.Forbidden):
        detail = "I don't have permission to do that."
    elif isinstance(original, discord.NotFound):
        detail = "Something was not found (message may have been deleted)."

    embed = discord.Embed(
        description=f"❌  {detail}",
        color=0xED4245,
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        pass  # If we can't respond, silently ignore



# ── Startup ───────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await bot.load_extension("cogs.music")
        print("🎵  Music cog loaded!")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
