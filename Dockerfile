FROM python:3.11-slim

# Install FFmpeg and Opus (required for audio streaming)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python 3.11-compatible stable deps (no git needed in image)
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Copy source code
COPY . .

# Run the bot
CMD ["python", "-u", "bot.py"]
