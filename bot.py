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
    # Unwrap the wrapper to get the real exception
    original = getattr(error, "original", error)
    msg = f"Something went wrong: `{original}`"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ── Startup ───────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await bot.load_extension("cogs.music")
        print("🎵  Music cog loaded!")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
