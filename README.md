# 🎵 Premium Discord Music Bot

A self-hosted Discord bot that streams audio from YouTube into voice channels.
Supports slash commands, queue management, loop modes, volume control, and rich embeds.

---

## ⚡ Quick Start (Local Testing)

### 1. Prerequisites

- **Python 3.10+** — [python.org](https://www.python.org/downloads/)
- **FFmpeg** — required for audio streaming
  - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html), extract, and add `bin/` folder to your system `PATH`
  - Or install via winget: `winget install ffmpeg`
  - Or install via chocolatey: `choco install ffmpeg`
- Verify FFmpeg is working: `ffmpeg -version`

### 2. Install Python dependencies

```powershell
cd music_discord_bot
pip install -r requirements.txt
```

### 3. Configure your bot token

```powershell
# Copy the example env file
copy .env.example .env
```

Then open `.env` and paste your bot token:
```
DISCORD_TOKEN=your_actual_token_here
```

> 💡 Get your token: [discord.com/developers](https://discord.com/developers/applications) → your app → **Bot** tab → **Reset Token**

### 4. Invite the bot to your server

Go to: [discord.com/developers](https://discord.com/developers/applications) → your app → **OAuth2** → **URL Generator**

Required scopes:
- `bot`
- `applications.commands`

Required bot permissions:
- `Connect` & `Speak` (Voice)
- `Send Messages`
- `Embed Links`
- `Read Message History`

### 5. Run the bot

```powershell
python bot.py
```

You should see:
```
✅  Logged in as YourBot#1234 (ID: ...)
📡  Connected to 1 guild(s)
🔄  Syncing slash commands...
✅  Slash commands synced!
```

---

## 🎮 Commands

| Command | Description |
|---|---|
| `/play <url or query>` | Play from YouTube URL or search query |
| `/skip` | Skip the current song |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/stop` | Stop and clear the queue |
| `/leave` | Disconnect the bot |
| `/nowplaying` | Show the current song |
| `/queue [page]` | View the queue |
| `/remove <position>` | Remove a song from the queue |
| `/move <from> <to>` | Move a song in the queue |
| `/shuffle` | Shuffle the queue |
| `/clear` | Clear the entire queue |
| `/volume <0-200>` | Set playback volume |
| `/loop` | Cycle loop mode: Off → Song → Queue |

---

## 🐳 Docker Deployment (Server)

When you're ready to move to your server:

```bash
# Clone/copy project to your server
cd music_discord_bot

# Create your .env with your token
cp .env.example .env
nano .env  # paste your token

# Start the container
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

> **Note:** FFmpeg is automatically installed inside the Docker image — no manual install needed on the server.

---

## 🏗️ Project Structure

```
music_discord_bot/
├── bot.py                  # Entry point
├── cogs/
│   └── music.py            # All slash commands
├── utils/
│   ├── ytdl.py             # yt-dlp audio extraction
│   ├── queue_manager.py    # Per-guild queue state
│   └── embeds.py           # Rich embed builders
├── .env                    # Your secrets (gitignored)
├── .env.example            # Template
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```
